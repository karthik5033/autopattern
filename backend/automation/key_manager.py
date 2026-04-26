"""
API Key Manager for AutoPattern.

Manages pools of Groq and Gemini API keys with round-robin rotation
and automatic cooldown recovery for exhausted keys.

Usage:
    from .key_manager import key_manager

    key = key_manager.get_best_key(preferred_provider="groq")
    try:
        # use key ...
    except RateLimitError:
        key_manager.mark_key_exhausted(key, "groq")
        key = key_manager.get_best_key()  # auto-fallback
"""

import json
import time
import threading
from pathlib import Path
from dotenv import dotenv_values


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_KEYS = 12
_COOLDOWN_SECONDS = 60

# Path to .env.local at project root (backend/automation/../../.env.local)
_ENV_LOCAL_PATH = Path(__file__).parent.parent.parent / ".env.local"
_KEY_STATE_PATH = Path(__file__).parent / ".key_state.json"


# ---------------------------------------------------------------------------
# KeyManager
# ---------------------------------------------------------------------------

class KeyManager:
    """Round-robin API key manager with per-key cooldown tracking.

    Loads ``GROQ_API_KEY_1`` … ``GROQ_API_KEY_12`` and
    ``GOOGLE_API_KEY_1`` … ``GOOGLE_API_KEY_12`` from the project-root
    ``.env.local`` file.  Keys that are missing or empty are silently
    skipped.

    Thread-safe: all mutable state is guarded by a lock so the manager
    can be shared between the REPL thread and the async API server.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()

        # Key pools — list of raw key strings
        self.groq_keys: list[str] = []
        self.gemini_keys: list[str] = []

        # Round-robin cursors
        self._groq_index: int = 0
        self._gemini_index: int = 0

        # Cooldown registry: key_string → expiry timestamp (time.monotonic)
        self._cooldowns: dict[str, float] = {}

        # Load keys on construction
        self._load_keys()

        # Restore round-robin indexes from previous run
        self._load_state()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load_keys(self) -> None:
        """Read .env.local and populate the key pools."""
        if not _ENV_LOCAL_PATH.exists():
            return

        env = dotenv_values(_ENV_LOCAL_PATH)

        for i in range(1, _MAX_KEYS + 1):
            # Groq keys
            groq_val = (env.get(f"GROQ_API_KEY_{i}") or "").strip()
            if groq_val:
                self.groq_keys.append(groq_val)

            # Gemini / Google keys
            gemini_val = (env.get(f"GOOGLE_API_KEY_{i}") or "").strip()
            if gemini_val:
                self.gemini_keys.append(gemini_val)

        # Also accept a bare GOOGLE_API_KEY as the first gemini key
        # if no numbered keys were found
        if not self.gemini_keys:
            bare = (env.get("GOOGLE_API_KEY") or "").strip()
            if bare:
                self.gemini_keys.append(bare)

    def reload(self) -> None:
        """Re-read .env.local (e.g. after user edits it at runtime)."""
        with self._lock:
            self.groq_keys.clear()
            self.gemini_keys.clear()
            self._groq_index = 0
            self._gemini_index = 0
            self._cooldowns.clear()
            self._load_keys()
            self._load_state()

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _load_state(self) -> None:
        """Restore round-robin indexes from disk (no lock needed — called
        only from __init__ / reload which run before any concurrent access)."""
        try:
            if _KEY_STATE_PATH.exists():
                data = json.loads(_KEY_STATE_PATH.read_text(encoding="utf-8"))
                gi = data.get("gemini_index", 0)
                ri = data.get("groq_index", 0)
                # Clamp to current pool size
                if self.gemini_keys:
                    self._gemini_index = gi % len(self.gemini_keys)
                if self.groq_keys:
                    self._groq_index = ri % len(self.groq_keys)
        except Exception:
            pass  # Corrupted / unreadable — start from 0

    def _save_state(self) -> None:
        """Write current round-robin indexes to disk.

        Must be called while ``self._lock`` is held."""
        try:
            _KEY_STATE_PATH.write_text(
                json.dumps({
                    "gemini_index": self._gemini_index,
                    "groq_index": self._groq_index,
                }),
                encoding="utf-8",
            )
        except Exception:
            pass  # Non-critical — worst case we re-use a key

    # ------------------------------------------------------------------
    # Cooldown helpers
    # ------------------------------------------------------------------

    def _is_cooled_down(self, key: str) -> bool:
        """Return True if *key* is NOT cooling down (i.e. it is usable)."""
        expiry = self._cooldowns.get(key)
        if expiry is None:
            return True
        if time.monotonic() >= expiry:
            # Cooldown expired — remove and allow
            del self._cooldowns[key]
            return True
        return False

    def mark_key_exhausted(self, key: str, provider: str) -> None:
        """Mark *key* as rate-limited for ``_COOLDOWN_SECONDS``.

        Args:
            key: The raw API key string.
            provider: ``"groq"`` or ``"gemini"`` (for logging; cooldown
                      is tracked by key string regardless of provider).
        """
        with self._lock:
            self._cooldowns[key] = time.monotonic() + _COOLDOWN_SECONDS

    # ------------------------------------------------------------------
    # Round-robin getters
    # ------------------------------------------------------------------

    def get_next_groq_key(self) -> str | None:
        """Return the next usable Groq key, or ``None`` if all are
        exhausted / cooling down."""
        with self._lock:
            return self._next_from_pool(
                self.groq_keys, "_groq_index"
            )

    def get_next_gemini_key(self) -> str | None:
        """Return the next usable Gemini key, or ``None`` if all are
        exhausted / cooling down."""
        with self._lock:
            return self._next_from_pool(
                self.gemini_keys, "_gemini_index"
            )

    def _next_from_pool(
        self, pool: list[str], index_attr: str
    ) -> str | None:
        """Scan *pool* starting from the current cursor, skipping keys
        that are cooling down.  Returns ``None`` if every key in the
        pool is unavailable.

        Must be called while ``self._lock`` is held.
        """
        if not pool:
            return None

        start = getattr(self, index_attr)
        n = len(pool)

        for offset in range(n):
            idx = (start + offset) % n
            candidate = pool[idx]
            if self._is_cooled_down(candidate):
                # Advance cursor past this key for next call
                setattr(self, index_attr, (idx + 1) % n)
                self._save_state()
                return candidate

        # All keys in pool are cooling down
        return None

    # ------------------------------------------------------------------
    # Smart getter with cross-pool fallback
    # ------------------------------------------------------------------

    def get_best_key(
        self, preferred_provider: str = "groq"
    ) -> tuple[str, str]:
        """Return ``(key, provider)`` using *preferred_provider* first,
        falling back to the other pool.

        Args:
            preferred_provider: ``"groq"`` or ``"gemini"``.

        Returns:
            A 2-tuple ``(api_key_string, provider_name)``.

        Raises:
            RuntimeError: If **all** keys across **both** pools are
                either exhausted or missing.
        """
        if preferred_provider == "groq":
            primary_fn, primary_name = self.get_next_groq_key, "groq"
            fallback_fn, fallback_name = self.get_next_gemini_key, "gemini"
        else:
            primary_fn, primary_name = self.get_next_gemini_key, "gemini"
            fallback_fn, fallback_name = self.get_next_groq_key, "groq"

        key = primary_fn()
        if key is not None:
            return key, primary_name

        key = fallback_fn()
        if key is not None:
            return key, fallback_name

        raise RuntimeError(
            "All API keys are exhausted or cooling down. "
            "Add more keys to .env.local or wait 60 seconds."
        )

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def status(self) -> dict:
        """Return a snapshot of pool sizes and cooldown counts."""
        with self._lock:
            now = time.monotonic()
            cooling = {
                k: round(exp - now, 1)
                for k, exp in self._cooldowns.items()
                if exp > now
            }
            return {
                "groq_keys_total": len(self.groq_keys),
                "gemini_keys_total": len(self.gemini_keys),
                "keys_cooling_down": len(cooling),
                "cooldowns": {
                    k[:8] + "...": secs for k, secs in cooling.items()
                },
            }

    def __repr__(self) -> str:
        return (
            f"<KeyManager groq={len(self.groq_keys)} "
            f"gemini={len(self.gemini_keys)} "
            f"cooling={len(self._cooldowns)}>"
        )

    # ------------------------------------------------------------------
    # Startup health check
    # ------------------------------------------------------------------

    def startup_health_check(self) -> dict:
        """Test each Gemini key with a minimal API call.

        Keys that return 403 PERMISSION_DENIED or have a zero quota
        are permanently marked exhausted for this session (cooldown set
        to a very large value so they never recover).

        Returns a summary dict:
            {"total": 12, "available": 9, "blocked": 3, "details": [...]}
        """
        import google.genai as genai

        total = len(self.gemini_keys)
        blocked = 0
        details: list[dict] = []

        _PERMANENT_COOLDOWN = 999_999  # ~11.5 days — effectively permanent

        for i, key in enumerate(self.gemini_keys):
            key_label = f"Key {i + 1}"
            masked = key[:6] + "..." + key[-4:]
            try:
                client = genai.Client(api_key=key)
                client.models.generate_content(
                    model="gemini-2.0-flash-lite",
                    contents="hi",
                )
                details.append({"key": masked, "status": "ok"})
            except Exception as e:
                err_msg = str(e).lower()
                if "403" in err_msg or "permission_denied" in err_msg or "limit:0" in err_msg:
                    # Permanently block this key
                    with self._lock:
                        self._cooldowns[key] = time.monotonic() + _PERMANENT_COOLDOWN
                    blocked += 1
                    details.append({"key": masked, "status": "blocked"})
                else:
                    # Transient error (429 etc.) — key may still be usable later
                    details.append({"key": masked, "status": f"warn: {str(e)[:60]}"})

        available = total - blocked
        return {
            "total": total,
            "available": available,
            "blocked": blocked,
            "details": details,
        }


# ---------------------------------------------------------------------------
# Module-level singleton — import this everywhere
# ---------------------------------------------------------------------------

key_manager = KeyManager()
