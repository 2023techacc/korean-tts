====================================================
Allinone Discord Bot - 설치 및 실행 방법
====================================================

기능: 포켓몬 RPG(/), 음악 재생(/, !), 급식·시간표(!), 한국어 TTS(!)


1) 파이썬 패키지 설치
---------------------
cmd 를 열고 이 폴더에서:

    py -m pip install -r requirements.txt

(discord.py, PyNaCl, python-dotenv, yt-dlp, requests,
 beautifulsoup4 가 한 번에 설치됩니다)

※ CLI TTS(tts.py)만 쓸 거라면 아무것도 설치할 필요가 없습니다.
  파이썬 기본 기능만으로 동작합니다.


2) FFmpeg 설치
--------------
https://www.gyan.dev/ffmpeg/builds/ 에서
ffmpeg-git-essentials.7z 를 받아 압축을 풉니다.

방법 A (권장) — 압축 푼 폴더 안의 bin 폴더를 Windows PATH 에 추가.
              그러면 아무 설정도 필요 없습니다.

방법 B — bin 폴더 안의 ffmpeg.exe 전체 경로를 .env 에 적습니다.
         (아래 3번 참고)

※ 예전 버전처럼 main.py 안의 ffmpeg_path 를 고칠 필요는 없습니다.


3) .env 파일 만들기
-------------------
.env.example 을 복사해서 .env 로 이름을 바꾸고 값을 채웁니다.

    DISCORD_TOKEN=여기에_봇_토큰
    # FFMPEG_PATH=C:\ffmpeg\bin\ffmpeg.exe   <- 방법 B 를 쓸 때만

봇 토큰은 https://discord.com/developers/applications 에서
앱 선택 -> 왼쪽 Bot -> Reset Token -> Copy.

⚠️ .env 는 절대 공유하거나 zip 에 넣어 배포하지 마세요.
   토큰이 유출되면 Reset Token 으로 새로 발급받으세요.


4) 봇 권한 설정 (Developer Portal)
----------------------------------
Bot -> Privileged Gateway Intents 에서
"MESSAGE CONTENT INTENT" 를 켜야 ! 명령어가 동작합니다.


5) 음성 파일
------------
sound 폴더에 .wav 파일들이 들어 있어야 !say 가 동작합니다.
(sound.zip 을 받았다면 이 폴더 안 sound/ 에 풀어주세요)


6) 실행
-------
    py main.py

(IDLE 이면 F5, VS Code 면 실행 버튼)

처음 실행하면 슬래시 명령어가 Discord 에 등록됩니다.
반영까지 최대 1시간 정도 걸릴 수 있습니다.


명령어 목록
-----------
포켓몬 (슬래시)
  /start              모험 시작, 스타터 지급
  /pokemon            내 포켓몬 목록 (번호 포함)
  /info <이름>        도감 정보
  /catch              야생 포켓몬 조우 + 볼 던지기 버튼
  /store <번호>       포켓몬 보관
  /unstorage <번호>   보관 해제
  /battle @상대       배틀 신청

음악 (슬래시 / 접두사 둘 다 가능)
  /play <검색어|URL>   !play  !p  !재생
  /stop                !stop  !s  !종료
  /leave               !leave !l  !나가
  /repeat on|off       !repeat on|off

학교
  !meal        오늘의 급식
  !timetable   오늘의 시간표

음성 TTS
  !join        음성 채널 입장
  !say <한글>  한글을 읽어줍니다
  !getout      음성 채널 퇴장


====================================================
CLI TTS (디스코드 없이 쓰기) - tts.py
====================================================

디스코드 없이 터미널에서 바로 한글을 읽게 할 수 있습니다. 설치할 패키지가
없고 FFmpeg 도 필요 없습니다 (파이썬 표준 라이브러리만 사용).

    py tts.py 안녕하세요

사용법, 발음 처리 규칙(연음화/비음화/유음화/격음화/경음화), 음질 처리
(음량 보정, 크로스페이드) 는 모두 TTS_README.txt 에 따로 정리했습니다.
디스코드 봇(main.py)의 !say 명령도 내부적으로 같은 엔진(korean_tts.py)을
씁니다.


참고: bot.py / pokemon_bot.py / voicetest.py / music/music_player.py 는
      main.py 로 합쳐지기 전의 예전 버전입니다. 실행할 필요 없습니다.
      voicetest.py 의 TTS 기능은 tts.py 가 대체합니다.
