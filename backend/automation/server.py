"""
FastAPI server for AutoPattern automation.

Provides REST API and WebSocket endpoints for browser automation.
"""

import asyncio
import logging
import signal
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .config import config
from .workflow_loader import WorkflowLoader, Workflow, WorkflowEvent
from .llm_client import LLMClient
from .automation_runner import AutomationRunner
from .task_manager import task_manager, TaskSource, BusyError

logger = logging.getLogger("autopattern.server")


# ============================================================================
# Shutdown flag
# ============================================================================

_shutting_down: bool = False


# ============================================================================
# Pydantic Models
# ============================================================================

class WorkflowEventModel(BaseModel):
    """Single workflow event from the extension."""
    event: str = Field(alias="event_type", default="unknown")
    timestamp: int = 0
    url: str = ""
    title: str = ""
    data: dict = Field(default_factory=dict)
    raw: dict = Field(default_factory=dict)
    automation: dict = Field(default_factory=dict)

    class Config:
        populate_by_name = True


class AutomateRequest(BaseModel):
    """Request to automate a workflow."""
    workflow_id: str = "1"
    events: list[WorkflowEventModel] = Field(default_factory=list)
    start_url: str = ""
    headless: bool = False
    # Optional: pre-generated task description (bypasses LLM generation if provided)
    task_description: Optional[str] = None
    # Optional: user-provided input values for form fields (username, password, etc.)
    input_values: Optional[dict[str, str]] = None


class TaskRequest(BaseModel):
    """Request to run automation from a task description."""
    task: str
    headless: bool = False
    # Optional: user-provided input values for form fields
    input_values: Optional[dict[str, str]] = None


class DescribeRequest(BaseModel):
    """Request to describe/analyze workflow events."""
    events: list[WorkflowEventModel]
    start_url: str = ""


class DescribeResponse(BaseModel):
    """Response from describe endpoint with structured workflow plan."""
    title: str
    description: str
    steps: list[dict]
    required_inputs: list[dict] = Field(default_factory=list)


class AutomateResponse(BaseModel):
    """Response from automation endpoint."""
    success: bool
    task_description: str = ""
    message: str = ""
    error: Optional[str] = None


class HealthResponse(BaseModel):
    """Health check response."""
    status: str = "ok"
    version: str = "0.2.4"
    active_tasks: int = 0
    draining: bool = False


class SettingsModel(BaseModel):
    """Application settings."""
    llm_model: str = "gemini-flash-latest"
    analysis_model: str = "gemini-pro-latest"
    headless: bool = False


class SettingsResponse(BaseModel):
    """Settings response."""
    settings: SettingsModel
    available_models: list[str] = []


# ============================================================================
# FastAPI App
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler with ordered shutdown."""
    global _shutting_down
    logger.info("AutoPattern API server starting...")
    print("🚀 AutoPattern API server starting...")
    yield
    # --- Ordered shutdown sequence ---
    _shutting_down = True
    print("👋 AutoPattern API server shutting down...")
    logger.info("Shutdown initiated — draining active tasks...")

    # 1. Drain running automation tasks (wait up to 10s, then cancel)
    await task_manager.drain(timeout=10.0)

    # 2. Close any browser instances created by the runner
    #    (chat.py manages its own browser; this covers server-only mode)
    logger.info("Cleanup complete.")
    print("✅ Graceful shutdown complete.")


app = FastAPI(
    title="AutoPattern API",
    description="Browser automation powered by AI",
    version="0.2.4",
    lifespan=lifespan,
)

# CORS middleware for extension integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow extension access
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# Endpoints
# ============================================================================

@app.get("/api/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    return HealthResponse(
        status="draining" if _shutting_down else "ok",
        active_tasks=task_manager.active_count,
        draining=_shutting_down,
    )


# Available Gemini models
AVAILABLE_MODELS = [
    "gemini-flash-latest",
    "gemini-pro-latest",
    "gemini-flash-lite-latest",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
]

# Runtime settings (can be modified via API)
runtime_settings = SettingsModel(
    llm_model=config.llm_model,
    headless=config.headless,
)


@app.get("/api/settings", response_model=SettingsResponse)
async def get_settings():
    """Get current settings."""
    return SettingsResponse(
        settings=runtime_settings,
        available_models=AVAILABLE_MODELS,
    )


@app.put("/api/settings", response_model=SettingsResponse)
async def update_settings(new_settings: SettingsModel):
    """Update settings."""
    global runtime_settings
    runtime_settings = new_settings
    
    # Update config object for components that use it
    config.llm_model = new_settings.llm_model
    config.headless = new_settings.headless
    
    return SettingsResponse(
        settings=runtime_settings,
        available_models=AVAILABLE_MODELS,
    )


@app.post("/api/describe", response_model=DescribeResponse)
async def describe_workflow(request: DescribeRequest):
    """
    Analyze workflow events and generate a structured description.
    
    Uses gemini-pro for high reasoning capability to convert raw events
    into a human-readable step-by-step plan that can be edited before execution.
    Also detects required input fields (username, password, 2FA, etc.).
    """
    try:
        # Convert events to dict format with all available data
        # Merge data and raw fields for backwards compatibility
        events = []
        for e in request.events:
            # Combine data and raw - raw takes precedence as it's the new format
            combined_data = {**e.data, **e.raw}
            events.append({
                "event_type": e.event,
                "timestamp": e.timestamp,
                "url": e.url,
                "title": e.title,
                "data": combined_data,
                "raw": e.raw,
                "automation": e.automation,
            })
        
        # Generate structured workflow steps using current settings
        llm_client = LLMClient(
            model=runtime_settings.llm_model,
            analysis_model=runtime_settings.analysis_model
        )
        result = llm_client.generate_workflow_steps(events, request.start_url)
        
        return DescribeResponse(
            title=result.get("title", "Workflow"),
            description=result.get("description", ""),
            steps=result.get("steps", []),
            required_inputs=result.get("required_inputs", [])
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/automate", response_model=AutomateResponse)
async def automate_workflow(request: AutomateRequest, background_tasks: BackgroundTasks):
    """
    Automate a workflow from recorded events.
    
    If task_description is provided, uses it directly.
    Otherwise, converts workflow events to a task description using LLM,
    then executes the automation using browser-use.
    
    input_values can contain user-provided credentials that will be passed
    to browser-use as sensitive_data for automatic form filling.
    """
    try:
        # If task_description is provided, use it directly (Direct execution flow)
        if request.task_description:
            task_description = request.task_description
        else:
            # Convert request events to Workflow object
            events = [
                WorkflowEvent(
                    event_type=e.event,
                    timestamp=e.timestamp,
                    url=e.url,
                    title=e.title,
                    data=e.data,
                )
                for e in request.events
            ]
            
            workflow = Workflow(workflow_id=request.workflow_id, events=events)
            
            # Override start_url if provided
            if request.start_url:
                # Insert a navigation event at the start
                events.insert(0, WorkflowEvent(
                    event_type="navigation",
                    timestamp=0,
                    url=request.start_url,
                    title="",
                    data={},
                ))
            
            # Generate task description using LLM with current settings
            llm_client = LLMClient(
                model=runtime_settings.llm_model,
                analysis_model=runtime_settings.analysis_model
            )
            task_description = llm_client.generate_task_description(workflow)
        
        # Prepare sensitive_data from input_values if provided
        sensitive_data = None
        if request.input_values:
            sensitive_data = request.input_values
            # Also inject values into task description using placeholders
            for key, value in request.input_values.items():
                placeholder = "{{" + key + "}}"
                # Use a safe placeholder format that browser-use understands
                task_description = task_description.replace(placeholder, f"<secret>{key}</secret>")
        
        # Reject new work if the server is shutting down
        if _shutting_down:
            raise HTTPException(status_code=503, detail="Server is shutting down")

        # Run automation with current settings and sensitive data
        runner = AutomationRunner(
            headless=request.headless if request.headless else runtime_settings.headless,
        )

        # Submit through the shared task manager (rejects if busy)
        try:
            info = task_manager.submit(
                runner.run_task(task_description, sensitive_data=sensitive_data),
                description=task_description,
                source=TaskSource.API,
            )
        except BusyError as e:
            raise HTTPException(status_code=409, detail=str(e))

        result = await info.asyncio_task
        
        return AutomateResponse(
            success=result["success"],
            task_description=task_description,
            message="Automation completed" if result["success"] else "Automation failed",
            error=result.get("error"),
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/automate/task", response_model=AutomateResponse)
async def automate_task(request: TaskRequest):
    """
    Run automation directly from a task description.
    
    Skips the LLM task generation step and executes the provided task directly.
    """
    try:
        # Reject new work if the server is shutting down
        if _shutting_down:
            raise HTTPException(status_code=503, detail="Server is shutting down")

        # Use request settings with fallback to runtime settings
        use_headless = request.headless if request.headless else runtime_settings.headless
        
        runner = AutomationRunner(
            headless=use_headless,
        )

        # Submit through the shared task manager (rejects if busy)
        try:
            info = task_manager.submit(
                runner.run_task(request.task),
                description=request.task,
                source=TaskSource.API,
            )
        except BusyError as e:
            raise HTTPException(status_code=409, detail=str(e))

        result = await info.asyncio_task
        
        return AutomateResponse(
            success=result["success"],
            task_description=request.task,
            message="Automation completed" if result["success"] else "Automation failed",
            error=result.get("error"),
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Server Runner
# ============================================================================

def run_server(host: str = "0.0.0.0", port: int = 5001):
    """Run the FastAPI server with graceful shutdown on SIGINT/SIGTERM."""
    import uvicorn

    uvi_config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        timeout_graceful_shutdown=10,   # seconds to drain before force-exit
    )
    server = uvicorn.Server(uvi_config)

    # uvicorn already handles SIGINT/SIGTERM to trigger shutdown,
    # but we install a handler to print a user-friendly message.
    _original_sigint = signal.getsignal(signal.SIGINT)

    def _shutdown_handler(signum, frame):
        sig_name = signal.Signals(signum).name
        print(f"\n⏳ Received {sig_name} — shutting down gracefully...")
        logger.info("Received %s, initiating graceful shutdown.", sig_name)
        # Restore default so a second Ctrl-C force-kills
        signal.signal(signal.SIGINT, _original_sigint)
        server.should_exit = True

    signal.signal(signal.SIGINT, _shutdown_handler)
    # SIGTERM is not available on Windows
    import sys
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, _shutdown_handler)

    server.run()
