# Pipelines

Status: reserved for implementation.

Orchestration is a simple command-line sequence (ingest → conform → resolve → controls and matching → graph sync → embed → agents → evaluate), runnable from PowerShell. No scheduler is planned for this case; Case 03 covers Airflow. Each step records a run in `ops` and is safe to rerun.
