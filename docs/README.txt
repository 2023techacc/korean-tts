[![Download](https://img.shields.io/github/v/release/2023techacc/korean-tts?include_prereleases&label=Download&color=blue)](https://github.com/2023techacc/korean-tts/releases/latest)

한국어 TTS - 웹 버전 (GitHub Pages)
====================================

이 폴더는 CLI(tts.py)/Android 앱과 같은 한국어 발음 엔진과 오디오 엔진을
브라우저에서 그대로 돌리는 정적(static) 웹사이트입니다. 서버 코드가
전혀 없고, 텍스트를 어디로도 전송하지 않습니다 - 모든 처리(음운 변환,
발음 조각 합치기, 재생, WAV 저장)가 사용자의 브라우저 안에서 끝납니다.

파일 구성
---------
index.html          - 페이지 본체 (입력창, 버튼, 설정 슬라이더)
style.css           - 스타일 (밝은/어두운 테마 자동 대응)
app.js              - 버튼/슬라이더 동작, 재생·저장 로직
korean-phonology.js - 한국어 음운 규칙 엔진 (korean_tts.py의 JS 이식판)
korean-audio.js     - 오디오 조립 엔진 (korean_tts.py의 JS 이식판,
                       단 .exe에만 있는 "세부 튜닝(overrides)" 기능은
                       웹 버전에는 없습니다 - CLI/APK와 동일한 기본
                       기능만 포함)
sound/               - 발음 조각 .wav 파일 (Allinone (2)\sound의 복사본).
                       목소리별 하위 폴더 (기본은 sound/default/) +
                       voices.json (아래 "여러 목소리" 참고)
tests/               - 개발용 자동 테스트 (파이썬 엔진과 결과가 정확히
                       일치하는지 확인하는 스크립트들). 사이트 동작에는
                       필요 없지만 나중에 엔진을 고칠 때 참고용으로
                       남겨둔 것이니 지우지 않아도 됩니다.

이 폴더는 c:\discordbot 저장소 안에서 이름이 "docs"인데, GitHub Pages가
브랜치 배포 방식에서 "/ (root)"와 "/docs" 두 가지 폴더 위치만 지원하기
때문입니다 - Discord 봇/CLI/Android 프로젝트와 한 저장소에 같이 두면서
Pages도 쓰려면 이 폴더가 반드시 "docs"라는 이름이어야 합니다.

로컬에서 미리 보기
------------------
그냥 index.html을 더블클릭해서 열면 "안 됩니다" - 최신 브라우저는
file:// 로 연 페이지에서 ES 모듈(import/export)이나 fetch()로 다른
파일(사운드 조각 등)을 읽는 것을 보안상 막습니다. 반드시 HTTP 서버로
띄워서 열어야 합니다. 예:

    cd C:\discordbot\docs
    python -m http.server 8000

그 다음 브라우저에서 http://localhost:8000 을 엽니다.
(GitHub Pages에 올리면 GitHub이 알아서 HTTP(S)로 서비스하므로 이 문제는
없습니다 - 이건 로컬 테스트할 때만 해당하는 이야기입니다.)

GitHub Pages에 올리는 방법
--------------------------
이 컴퓨터에는 GitHub 로그인/원격 저장소가 설정되어 있지 않아서, 아래 두
단계는 직접 해주셔야 합니다 (저장소 자체(git init/add/commit)는 이미
준비되어 있습니다 - c:\discordbot 폴더 전체가 하나의 저장소입니다).

1) GitHub에 이 저장소 올리기 (PowerShell 또는 명령 프롬프트에서)

    cd C:\discordbot
    git remote add origin https://github.com/<본인계정>/<저장소이름>.git
    git push -u origin main

   (먼저 github.com에서 "New repository"로 빈 저장소를 만들어두세요 -
   README 등은 추가하지 말고 "Create repository"만 누르면 됩니다.
   "git"이 설치되어 있지 않다고 나오면 https://git-scm.com/download/win
   에서 설치 후 다시 시도하세요.)

2) GitHub Pages 켜기
   저장소 페이지 -> Settings -> 왼쪽 메뉴 Pages
   "Build and deployment" -> Source를 "Deploy from a branch"로 설정
   Branch를 "main", 폴더를 "/docs"로 선택 -> Save
   (반드시 "/docs"를 선택해야 합니다 - "/ (root)"를 고르면 Discord
   봇/Python 코드가 그대로 웹에 노출되는 페이지가 만들어집니다.)

3) 몇 분 기다리면 다음 주소에서 열립니다:
   https://<본인계정>.github.io/<저장소이름>/

나중에 발음 파일을 다시 녹음하거나 엔진을 고치면
--------------------------------------------------
- sound\ 파일을 바꿨다면: 프로젝트 루트의 sync-sound-assets.bat 을
  실행하세요 - Android 자산뿐 아니라 이 폴더(docs\sound)에도 자동으로
  복사됩니다. 그 다음 다시 git add/commit/push 해야 실제 사이트에
  반영됩니다.
- korean_tts.py의 음운/오디오 로직을 고쳤다면: korean-phonology.js와
  korean-audio.js도 같은 내용으로 손으로 맞춰 고쳐야 합니다 (자동 동기화
  되지 않습니다). tests\ 안의 스크립트로 두 엔진의 결과가 정확히
  일치하는지 다시 확인하는 것을 권장합니다.

여러 목소리 (multi-voice)
-------------------------
sound\voices.json 이 사용 가능한 목소리 목록입니다 (sync-sound-assets.bat
이 자동으로 만들어줍니다 - CLI/Android처럼 폴더를 직접 나열할 수 없는
정적 사이트라서 필요한 파일입니다). 목소리를 하나만 쓴다면 이 파일이나
설정 화면의 드롭다운을 신경 쓸 필요가 없습니다. 여러 목소리를 추가하는
방법은 ..\Allinone (2)\TTS_README.txt 의 "여러 목소리" 항목 참고 - 그
폴더 구조를 그대로 sync-sound-assets.bat 이 이 sound\ 로도 복사해줍니다.

각 항목은 {"name": ..., "type": ...} 형태이고, 이 페이지는 아직 "pieces"
(조각 방식) 종류만 재생할 수 있습니다 - 다른 사운드 뱅크 종류(예: 완전한
음절 통째로 녹음, TTS_README.txt의 "사운드 뱅크 종류" 항목 참고)의
목소리는 voices.json에는 나오지만 설정 화면 드롭다운에는 나타나지
않습니다 (app.js의 KNOWN_BANK_TYPES 목록에 없는 종류는 걸러냄).

CLI/APK/EXE와의 차이점
-----------------------
- 설정(속도/간격/볼륨/받침 뒤 간격/목소리)은 CLI, Android 앱과 동일하게
  제공됩니다. 설정값은 이 브라우저의 localStorage에 저장되어 다음에
  열 때도 유지되지만, 기기/브라우저를 바꾸면 초기화됩니다.
- .exe에만 있는 "발음 조각별 세부 튜닝(볼륨 dB, 앞/뒤 자르기)" 기능은
  웹 버전에는 없습니다. 그 기능이 필요하면 Allinone (2)\KoreanTTS.exe
  를 사용하세요.
