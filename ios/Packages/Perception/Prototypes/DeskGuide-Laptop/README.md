# DeskGuide Laptop

CPU RGB-D hand guidance prototype for Reflex. Automatic beverage-can targeting uses the existing YOLO11s model's cup/class-41 prediction. Hand tracking uses the official MediaPipe hand task, with independently maintained geometry, target tracking and directional audio. No Proprio source or reference environment is needed at runtime.

## Launch

From this prototype directory, run setup.ps1 with Python 3.12 first on a new machine. Keep PathOn streaming on the iPhone connected to the existing TONY 0864 hotspot. Close older camera clients, then run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\launch.ps1 -PhoneIP <CURRENT_PATHON_IP>
```

Use PathOn's current address if it changes. Execution policy applies only to the launched PowerShell process. This laptop's .venv uses the project-local .runtime/python; a fresh installation can run setup.ps1 with a compatible Python 3.12 including Tcl/Tk.

Controls: R searches again, M mutes, O rotates, F11 toggles fullscreen, Escape exits fullscreen. Detection is automatic. No rectangle selection.

Hand tracking, depth and guidance status appear in the existing panel. Estimated distances are labeled and display-only. Guidance requires current supported hand/target geometry within 300 ms. Missing depth or lost tracking pauses audio; no automatic successful-grasp claim is made.

YOLO retains input 640/confidence 0.2 and runs as one nonblocking background job. Checked current-image tracking and current paired depth bridge detections, with faster search when lost. Frame acquisition uses the latest complete paired RGB-D packet. Internal session logs contain raw detector boxes/classes/scores and target-selection/depth rejection reasons.

[Latest latency fix, measured evidence and Claude reviews](ORCHESTRATION_AND_LATENCY_FIX.md). [Independence/provenance audit](CLEANUP_AND_ACQUISITION.md). [Dependency licenses](THIRD_PARTY.md). Older reference evaluation documents are historical. No hardware success or post-change FPS is claimed. Native iOS integration remains future work.
