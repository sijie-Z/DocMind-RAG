Write-Host "Starting RAG System Initialization..." -ForegroundColor Green

# Configuration
$PYTHON_EXE = "D:\Programming\Python 3.12.8\python.exe"

# 1. Install Dependencies
Write-Host "Step 1: Checking/Installing Dependencies..." -ForegroundColor Yellow
& $PYTHON_EXE -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "Dependency installation failed. Please check your python environment." -ForegroundColor Red
    Write-Host "Trying to continue anyway..." -ForegroundColor DarkGray
}

# 2. Start API
Write-Host "Step 2: Starting API Server (Producer)..." -ForegroundColor Yellow
# Start in a new window to keep logs visible
# We use a trick to pass the command with quotes to Start-Process
$apiCmd = "& '$PYTHON_EXE' -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $apiCmd

# 3. Start Worker
Write-Host "Step 3: Starting Worker Service (Consumer)..." -ForegroundColor Yellow
# Set PYTHONPATH and start worker in new window
$workerCmd = "$env:PYTHONPATH = '.'; & '$PYTHON_EXE' worker/doc_consumer.py"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $workerCmd

Write-Host "RAG System Startup Initiated!" -ForegroundColor Green
Write-Host "Please check the two new PowerShell windows for logs."
Write-Host "API: http://localhost:8000/docs"
