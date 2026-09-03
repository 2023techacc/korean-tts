@echo off
REM Syncs "Allinone (2)\sound" (the one canonical copy of the voice files)
REM into the Android project's bundled assets and the web version's copy.
REM The CLI and Discord bot read sound\ straight off disk every time, so
REM they need nothing extra when you change a file there. The Android app
REM and the web version are different: each has its own baked-in copy, so
REM a change on disk does nothing until you run this (and, for Android,
REM rebuild the app).
REM
REM Written with single-line "if ... goto" instead of multi-line "if (...)"
REM blocks on purpose: cmd.exe's block parser gets confused by the literal
REM "(2)" in the Allinone (2) path when it appears inside a parenthesized
REM if-block, so this avoids that pitfall entirely.

set "ROOT=%~dp0"
set "SRC=%ROOT%Allinone (2)\sound"
set "DST_ANDROID=%ROOT%KoreanTTS-Android\app\src\main\assets\sound"
set "DST_WEB=%ROOT%docs\sound"

if not exist "%SRC%" goto :nosrc

echo === Checking "Allinone (2)\sound" for missing/renamed samples ===
pushd "%ROOT%Allinone (2)"
py tts.py --check
set "CHECKRESULT=%ERRORLEVEL%"
popd

if "%CHECKRESULT%"=="0" goto :dosync
echo.
echo tts.py --check found a problem (see above). Fix it first if you can -
echo otherwise press any key to sync anyway, or Ctrl+C to stop.
pause >nul

:dosync
echo.
echo === Syncing into the Android project's assets ===
echo   %SRC%
echo   -^> %DST_ANDROID%
echo.

robocopy "%SRC%" "%DST_ANDROID%" *.wav /MIR /NDL /NJH

if %ERRORLEVEL% GEQ 8 goto :robofail_android

echo.
echo === Syncing into the web version ===
echo   %SRC%
echo   -^> %DST_WEB%
echo.

robocopy "%SRC%" "%DST_WEB%" *.wav /MIR /NDL /NJH

if %ERRORLEVEL% GEQ 8 goto :robofail_web

echo.
echo === Writing docs\voices.json (voice list for the web version) ===
REM The CLI/Android/exe can just list a folder to find voices at runtime;
REM a static site can't, so this writes the list out as JSON instead.
REM sound\ itself is always "default"; any subfolder with a .wav in it
REM (narrator2\, etc - see TTS_README.txt's multi-voice section) is
REM another voice.
set "VOICES_JSON=%DST_WEB%\voices.json"
> "%VOICES_JSON%" echo {
>> "%VOICES_JSON%" echo   "voices": [
set "FIRST=1"
call :write_voice default
for /d %%D in ("%SRC%\*") do call :maybe_write_voice "%%~fD"
>> "%VOICES_JSON%" echo.
>> "%VOICES_JSON%" echo   ]
>> "%VOICES_JSON%" echo }
echo   %VOICES_JSON%

echo.
echo Done.
echo   - Android still needs a rebuild to pick this up:
echo     Android Studio -^> Run, or Build ^> Build Bundle(s^) / APK(s^) -^> Build APK(s^)
echo   - The web version picks it up immediately (just refresh the page);
echo     if it's already published, re-push/re-deploy to update the live site.
echo   - The .exe bundles its own copy of sound\ too - rebuild it with
echo     BUILD_EXE.txt's command if you want the change baked into the .exe.
goto :eof

:maybe_write_voice
if /i "%~n1"=="default" goto :eof
dir /a-d "%~1\*.wav" >nul 2>&1
if errorlevel 1 goto :eof
call :write_voice "%~n1"
goto :eof

:write_voice
if "%FIRST%"=="1" (
    set "FIRST=0"
    >> "%VOICES_JSON%" echo     "%~1"
) else (
    >> "%VOICES_JSON%" echo     ,"%~1"
)
goto :eof

:nosrc
echo Can't find %SRC%
exit /b 1

:robofail_android
echo.
echo robocopy reported an error syncing to Android - check the paths above.
exit /b 1

:robofail_web
echo.
echo robocopy reported an error syncing to the web version - check the paths above.
exit /b 1
