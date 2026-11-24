# Coach Gemini error recovery

Chat and run ingestion should identify billing, model and timeout failures in Vietnamese.
Classify provider failures at the call boundary. Keep activities pending after failed generation.
Stop a scheduled batch on a shared billing or model failure and resume on the next scheduled run.

Use the coach client and a small generation probe before saving a changed model in either settings form.
A failed probe must leave the cached and persisted settings intact. Run synchronous probes in a worker thread.
No schema or prompt changes are needed. Test failure classification, scheduler behavior and settings saves.
