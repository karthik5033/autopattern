// background.js (service worker)
// Manages workflow recording and storage (FIXED)

const API_BASE_URL = 'http://127.0.0.1:5001';

let recordingState = {
    isRecording: false,
    currentWorkflow: [],
    workflowName: null,
    startTime: null
};

// Buffer events that arrive before recording starts
let pendingEvents = [];

let dbInstance;

// ---------- IndexedDB ----------
function getDB() {
    return new Promise((resolve, reject) => {
        if (dbInstance) return resolve(dbInstance);

        const request = indexedDB.open('AutomationDB', 1);

        request.onupgradeneeded = (e) => {
            const db = e.target.result;
            if (!db.objectStoreNames.contains('workflows')) {
                db.createObjectStore('workflows', {
                    keyPath: 'id',
                    autoIncrement: true
                });
            }
        };

        request.onsuccess = (e) => {
            dbInstance = e.target.result;
            resolve(dbInstance);
        };

        request.onerror = () => reject(request.error);
    });
}

// ---------- Recording Control ----------
function startRecording(workflowName) {
    recordingState.isRecording = true;
    recordingState.workflowName = workflowName || `Workflow ${new Date().toLocaleString()}`;
    recordingState.startTime = Date.now();

    // Clear all previous events for a fresh start
    recordingState.currentWorkflow = [];
    pendingEvents = [];

    console.log('Recording started');
}

async function stopRecording() {
    if (!recordingState.isRecording) return null;

    recordingState.isRecording = false;

    const events = recordingState.currentWorkflow;
    
    // Determine start URL from first navigation event or first event's URL
    let startUrl = '';
    if (events.length > 0) {
        const firstNav = events.find(e => e.event_type === 'navigation' || e.event === 'navigation');
        startUrl = firstNav?.url || events[0]?.url || '';
    }

    const workflow = {
        name: recordingState.workflowName,
        createdAt: recordingState.startTime,
        events: events,
        eventCount: events.length,
        schema: 'workflow-v1',
        // AI-generated description (will be populated asynchronously)
        description: null,
        steps: null,
        descriptionStatus: 'pending'
    };

    const db = await getDB();
    const tx = db.transaction('workflows', 'readwrite');
    const store = tx.objectStore('workflows');
    
    // Add the workflow and get the generated ID
    const addRequest = store.add(workflow);
    
    return new Promise((resolve, reject) => {
        addRequest.onsuccess = async () => {
            const workflowId = addRequest.result;
            workflow.id = workflowId;
            
            console.log('Workflow saved, fetching AI description...');
            
            // Fetch AI description asynchronously
            fetchWorkflowDescription(workflowId, events, startUrl);
            
            recordingState.currentWorkflow = [];
            recordingState.workflowName = null;
            recordingState.startTime = null;
            
            resolve(workflow);
        };
        
        addRequest.onerror = () => {
            reject(addRequest.error);
        };
    });
}

// Fetch AI description for a workflow
async function fetchWorkflowDescription(workflowId, events, startUrl) {
    try {
        const response = await fetch(`${API_BASE_URL}/api/describe`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                events: events,
                start_url: startUrl
            })
        });
        
        if (!response.ok) {
            throw new Error(`API error: ${response.status}`);
        }
        
        const result = await response.json();
        
        // Update the workflow in IndexedDB with the description
        const db = await getDB();
        const tx = db.transaction('workflows', 'readwrite');
        const store = tx.objectStore('workflows');
        const getRequest = store.get(workflowId);
        
        getRequest.onsuccess = () => {
            const workflow = getRequest.result;
            if (workflow) {
                workflow.aiTitle = result.title;
                workflow.description = result.description;
                workflow.steps = result.steps;
                workflow.descriptionStatus = 'success';
                store.put(workflow);
                
                console.log('Workflow description saved:', result.description);
                
                // Notify dashboard to refresh
                chrome.runtime.sendMessage({ action: 'refresh_dashboard' }).catch(() => {});
            }
        };
        
    } catch (error) {
        console.error('Failed to fetch workflow description:', error);
        
        // Update status to failed
        const db = await getDB();
        const tx = db.transaction('workflows', 'readwrite');
        const store = tx.objectStore('workflows');
        const getRequest = store.get(workflowId);
        
        getRequest.onsuccess = () => {
            const workflow = getRequest.result;
            if (workflow) {
                workflow.descriptionStatus = 'failed';
                workflow.descriptionError = error.message;
                store.put(workflow);
            }
        };
    }
}

// ---------- Messaging ----------
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    (async () => {

        if (msg.action === 'GET_RECORDING_STATE') {
            sendResponse({ isRecording: recordingState.isRecording });
            return;
        }

        if (msg.action === 'START_RECORDING') {
            startRecording(msg.workflowName);
            sendResponse({ status: 'ok' });
            return;
        }

        if (msg.action === 'STOP_RECORDING') {
            const workflow = await stopRecording();
            chrome.runtime.sendMessage({ action: 'refresh_dashboard' }).catch(() => {});
            sendResponse({ status: 'ok', workflow });
            return;
        }

        if (msg.action === 'RECORD_EVENT') {
            if (recordingState.isRecording) {
                recordingState.currentWorkflow.push(msg.event);
            } else {
                pendingEvents.push(msg.event);
            }
            sendResponse({ status: 'ok' });
            return;
        }

        if (msg.action === 'GET_WORKFLOWS') {
            const db = await getDB();
            const req = db.transaction('workflows', 'readonly')
                .objectStore('workflows')
                .getAll();

            req.onsuccess = () => {
                sendResponse({ status: 'ok', workflows: req.result });
            };
            return true;
        }

        if (msg.action === 'DELETE_WORKFLOW') {
            const db = await getDB();
            const tx = db.transaction('workflows', 'readwrite');
            tx.objectStore('workflows').delete(msg.id);
            tx.oncomplete = () => sendResponse({ status: 'ok' });
            return true;
        }

        if (msg.action === 'RENAME_WORKFLOW') {
            const db = await getDB();
            const store = db.transaction('workflows', 'readwrite').objectStore('workflows');
            const req = store.get(msg.id);

            req.onsuccess = () => {
                const wf = req.result;
                if (wf) {
                    wf.name = msg.newName;
                    store.put(wf);
                    sendResponse({ status: 'ok' });
                }
            };
            return true;
        }

        if (msg.action === 'UPDATE_WORKFLOW') {
            const db = await getDB();
            const store = db.transaction('workflows', 'readwrite').objectStore('workflows');
            const req = store.get(msg.id);

            req.onsuccess = () => {
                const wf = req.result;
                if (wf) {
                    // Apply updates
                    if (msg.updates) {
                        Object.assign(wf, msg.updates);
                    }
                    store.put(wf);
                    sendResponse({ status: 'ok' });
                } else {
                    sendResponse({ status: 'error', message: 'Workflow not found' });
                }
            };
            
            req.onerror = () => {
                sendResponse({ status: 'error', message: 'Database error' });
            };
            return true;
        }

        // Re-fetch description for a workflow
        if (msg.action === 'REFETCH_DESCRIPTION') {
            const db = await getDB();
            const store = db.transaction('workflows', 'readonly').objectStore('workflows');
            const req = store.get(msg.id);

            req.onsuccess = () => {
                const wf = req.result;
                if (wf && wf.events) {
                    let startUrl = '';
                    if (wf.events.length > 0) {
                        const firstNav = wf.events.find(e => e.event_type === 'navigation' || e.event === 'navigation');
                        startUrl = firstNav?.url || wf.events[0]?.url || '';
                    }
                    fetchWorkflowDescription(msg.id, wf.events, startUrl);
                    sendResponse({ status: 'ok' });
                } else {
                    sendResponse({ status: 'error', message: 'Workflow not found' });
                }
            };
            return true;
        }

    })();

    return true;
});
