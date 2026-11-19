## Main Idea
Create ai fraud detection from voice assistant, use microphone to capture audio, then process it to detect fraud. Make the background process laya ai for decision model, speech to text with whisper model with context, and also make realtime fraud detection with microphone. Make it in real time detection. The output use websocket to send to frontend. Save the data speech to text every the real time process fraud detection in the folder ./log so we can use it for analyze and improve the model later from data real user. Make sure save log with timestamp of the file.

## structure project
.
└── ai_fraud_detection_microphone/
    ├── backend/
    │   ├── app/
    │   │   ├── main.py
    │   │   └── ai_manager.py
    │   └── internal/
    │       ├── ai_runtime/
    │       │   ├── background_process/
    │       │   │   ├── laya_ai_process
    │       │   │   └── speech_to_text
    │       │   └── ai_fraud_detection
    │       ├── client
    │       ├── delivery
    │       └── utils
    ├── config/
    │   ├── microphone.json
    │   └── config.yaml
    └── log/
        ├── 2026-11-11
        └── 2026-11-12