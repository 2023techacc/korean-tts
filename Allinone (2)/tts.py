"""Korean TTS from the command line - no Discord required.

    py tts.py 안녕하세요              말하기
    py tts.py -o hi.wav 안녕하세요    파일로 저장
    py tts.py -p 국물이 좋다          실제 발음(연음/비음화/격음화 등 적용)만 보기: '궁무리 조타'
    py tts.py -s 안녕하세요           재생 없이 샘플 순서만 보기
    py tts.py --speed 1.5 안녕하세요  1.5배 빠르게 재생 (0.5~2.0)
    py tts.py --voice narrator2 안녕  다른 목소리로 재생 (sound/narrator2/ 필요)
    py tts.py --list-voices           사용 가능한 목소리 목록
    py tts.py                         대화형 모드
    py tts.py --check                 빠진 음성 파일 점검
"""

import argparse
import os
import sys

import korean_tts as ktts

DEFAULT_SOUND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sound")
DEFAULT_OVERRIDES_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ktts.DEFAULT_OVERRIDES_FILENAME
)


def info(msg=""):
    """print() that survives a console whose codepage can't show Hangul."""
    try:
        print(msg)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(msg.encode(enc, "replace").decode(enc))


def speak(text, args, allow_play=True):
    """Render `text`, then save it / play it / just report it.

    allow_play is False for the one-shot `-s`/`-p` dry runs; interactive mode
    passes True so that :show/:pron annotate the line *and* still speak it.
    """
    if args.pronounce:
        info("  발음: " + ktts.text_to_pronunciation(text))

    names = ktts.text_to_samples(text)

    if args.show:
        info("  샘플: " + (" ".join(names) if names else "(없음)"))

    if not names or all(n == ktts.PAUSE for n in names):
        info("읽을 수 있는 한글이 없습니다.")
        return False

    try:
        groups = ktts.text_to_groups(text)
        sound_dir = ktts.voice_dir(args.sound_dir, args.voice)
        overrides_path = ktts.overrides_path_for_voice(args.overrides, args.voice)
        overrides = {} if args.no_overrides else ktts.load_overrides(overrides_path)
        track, missing = ktts.build_audio(
            groups, sound_dir, gap_ms=args.gap, fade_ms=args.fade,
            normalize=not args.no_normalize, crossfade=not args.no_crossfade,
            speed=args.speed, stop_gap_ms=args.stop_gap, overrides=overrides,
        )
    except ktts.AudioError as e:
        info(f"오디오 오류: {e}")
        return False

    if missing:
        info(f"⚠️  음성 파일 {len(missing)}개 없음: {', '.join(sorted(set(missing)))}")
    if not len(track):
        info("재생할 오디오가 없습니다.")
        return False

    if args.output:
        with open(args.output, "wb") as f:
            f.write(ktts.to_wav_bytes(track))
        info(f"💾 {args.output} ({ktts.duration(track):.2f}초)")
        return True

    if not allow_play:
        info(f"   ({ktts.duration(track):.2f}초)")
        return True

    try:
        ktts.play(ktts.to_wav_bytes(track))
    except ktts.AudioError as e:
        info(f"재생 실패: {e}")
        return False
    return True


def interactive(args):
    info("한국어 TTS - 읽을 문장을 입력하세요.")
    info("  :q 종료   :show 샘플순서 토글   :pron 발음 토글   :gap <ms> 쉼길이")
    info("  :save <파일> 마지막 문장 저장   :norm 음량 보정 토글   :cross 크로스페이드 토글")
    info(f"  :speed <배속> 재생 속도 ({ktts.MIN_SPEED}~{ktts.MAX_SPEED}, 기본 1.0)")
    info(f"  :stopgap <ms> 받침 ㄱㄷㅂ 뒤 간격 (기본 {ktts.DEFAULT_STOP_GAP_MS}ms, 0이면 끔)")
    info(f"  :voice <이름> 목소리 바꾸기   :voices 사용 가능한 목소리 목록 (현재: {args.voice})")
    info()

    last = None
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            info()
            return 0

        if not line:
            continue

        if line in (":q", ":quit", ":exit"):
            return 0

        if line == ":show":
            args.show = not args.show
            info(f"  샘플 순서 표시: {'켜짐' if args.show else '꺼짐'}")
            continue

        if line == ":pron":
            args.pronounce = not args.pronounce
            info(f"  발음 표시: {'켜짐' if args.pronounce else '꺼짐'}")
            continue

        if line == ":norm":
            args.no_normalize = not args.no_normalize
            info(f"  음량 보정: {'꺼짐' if args.no_normalize else '켜짐'}")
            continue

        if line == ":cross":
            args.no_crossfade = not args.no_crossfade
            info(f"  크로스페이드: {'꺼짐' if args.no_crossfade else '켜짐'}")
            continue

        if line.startswith(":speed"):
            parts = line.split()
            try:
                value = float(parts[1]) if len(parts) == 2 else None
            except ValueError:
                value = None
            if value is None:
                info("  사용법: :speed 1.5")
            else:
                args.speed = max(ktts.MIN_SPEED, min(ktts.MAX_SPEED, value))
                info(f"  재생 속도: {args.speed}x")
            continue

        if line.startswith(":stopgap"):
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                args.stop_gap = int(parts[1])
                info(f"  받침 ㄱㄷㅂ 뒤 간격: {args.stop_gap}ms")
            else:
                info(f"  사용법: :stopgap {ktts.DEFAULT_STOP_GAP_MS}")
            continue

        if line == ":voices":
            voices = ktts.list_voices(args.sound_dir)
            info("  사용 가능한 목소리: " + ", ".join(voices) + f"  (현재: {args.voice})")
            continue

        if line.startswith(":voice"):
            parts = line.split(maxsplit=1)
            if len(parts) != 2:
                info("  사용법: :voice narrator2   (:voices 로 목록 확인)")
            else:
                name = parts[1].strip()
                if name not in ktts.list_voices(args.sound_dir):
                    info(f"  그런 목소리가 없습니다: {name}  (:voices 로 목록 확인)")
                else:
                    args.voice = name
                    info(f"  목소리: {args.voice}")
            continue

        if line.startswith(":gap"):
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                args.gap = int(parts[1])
                info(f"  쉼 길이: {args.gap}ms")
            else:
                info("  사용법: :gap 300")
            continue

        if line.startswith(":save"):
            parts = line.split(maxsplit=1)
            if len(parts) != 2:
                info("  사용법: :save out.wav")
            elif last is None:
                info("  먼저 읽을 문장을 입력하세요.")
            else:
                saved = argparse.Namespace(**vars(args))
                saved.output = parts[1].strip()
                speak(last, saved, allow_play=False)
            continue

        if line.startswith(":"):
            info(f"  알 수 없는 명령: {line}")
            continue

        speak(line, args)
        last = line


def check(args):
    """Report which samples the engine can ask for but sound/ doesn't have."""
    sound_dir = ktts.voice_dir(args.sound_dir, args.voice)
    if not os.path.isdir(sound_dir):
        info(f"❌ 음성 폴더가 없습니다: {sound_dir}")
        return 1

    have = {f[:-4] for f in os.listdir(sound_dir) if f.lower().endswith(".wav")}
    needed = ktts.all_reachable_samples()

    info(f"목소리     : {args.voice}")
    info(f"음성 폴더 : {sound_dir}")
    info(f"보유 파일 : {len(have)}개")
    info(f"필요 음절 : {len(needed)}개 (한글 11,172자를 모두 읽는 데 필요한 조각)")

    missing = sorted(needed - have)
    extra = sorted(have - needed)

    if missing:
        info(f"\n❌ 없는 파일 {len(missing)}개:")
        for i in range(0, len(missing), 12):
            info("   " + " ".join(missing[i:i + 12]))
    else:
        info("\n✅ 모든 한글을 읽을 수 있습니다.")

    if extra:
        info(f"\nℹ️  쓰이지 않는 파일 {len(extra)}개: {' '.join(extra)}")
    return 1 if missing else 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="tts.py",
        description="한국어 텍스트를 sound/ 의 음절 샘플로 읽어줍니다.",
        epilog="인자 없이 실행하면 대화형 모드로 들어갑니다.",
    )
    parser.add_argument("text", nargs="*", help="읽을 한국어 텍스트")
    parser.add_argument("-o", "--output", metavar="FILE", help="재생 대신 .wav 파일로 저장")
    parser.add_argument("-s", "--show", action="store_true",
                        help="사용되는 샘플 순서를 출력 (단독 사용 시 재생하지 않음)")
    parser.add_argument("-p", "--pronounce", action="store_true",
                        help="연음이 적용된 실제 발음을 출력 (예: 옷이 -> 오시) "
                             "(단독 사용 시 재생하지 않음)")
    parser.add_argument("--gap", type=int, default=300, metavar="MS",
                        help="띄어쓰기/문장부호에서 쉬는 길이, 기본 300ms")
    parser.add_argument("--fade", type=int, default=5, metavar="MS",
                        help="샘플 이음새 페이드 길이, 기본 5ms (0이면 끔)")
    parser.add_argument("--no-normalize", action="store_true",
                        help="음량 자동 보정을 끄고 원본 음량 그대로 재생/저장")
    parser.add_argument("--no-crossfade", action="store_true",
                        help="이중모음/받침 크로스페이드를 끄고 조각을 그대로 이어붙임")
    parser.add_argument("--speed", type=float, default=1.0, metavar="X",
                        help=f"재생 속도 배율, 기본 1.0 ({ktts.MIN_SPEED}~{ktts.MAX_SPEED}로 자동 제한, "
                             "속도에 따라 음높이도 함께 변함)")
    parser.add_argument("--stop-gap", type=int, default=ktts.DEFAULT_STOP_GAP_MS, metavar="MS",
                        help=f"받침 ㄱ/ㄷ/ㅂ(ㅋ,ㄲ,ㅌ 등 포함) 뒤에 추가로 쉬는 길이, "
                             f"기본 {ktts.DEFAULT_STOP_GAP_MS}ms (0이면 끔)")
    parser.add_argument("--overrides", default=DEFAULT_OVERRIDES_PATH, metavar="FILE",
                        help="음성 조각별 미세조정 파일 위치, 기본 ./sound_overrides.json "
                             "(고급 설정 GUI에서 저장한 파일 - 없으면 그냥 무시됨)")
    parser.add_argument("--no-overrides", action="store_true",
                        help="음성 조각별 미세조정을 끄고 자동 처리 결과만 사용")
    parser.add_argument("--sound-dir", default=DEFAULT_SOUND_DIR, metavar="DIR",
                        help="음성 파일 폴더, 기본 ./sound")
    parser.add_argument("--voice", default=ktts.DEFAULT_VOICE, metavar="NAME",
                        help="사용할 목소리, 기본 'default' (--sound-dir 바로 아래의 파일들). "
                             "다른 목소리는 --sound-dir/<이름>/ 에 같은 파일들을 넣어두면 "
                             "--voice <이름> 으로 선택할 수 있음 (--list-voices 로 확인)")
    parser.add_argument("--list-voices", action="store_true",
                        help="--sound-dir 아래에서 사용 가능한 목소리 목록을 출력하고 종료")
    parser.add_argument("--check", action="store_true", help="빠진 음성 파일을 점검하고 종료")
    args = parser.parse_args(argv)

    if args.list_voices:
        for name in ktts.list_voices(args.sound_dir):
            info(name)
        return 0

    clamped = max(ktts.MIN_SPEED, min(ktts.MAX_SPEED, args.speed))
    if clamped != args.speed:
        info(f"⚠️  --speed {args.speed} 는 범위를 벗어나 {clamped}로 조정됩니다.")
        args.speed = clamped

    if args.check:
        return check(args)

    voice_sound_dir = ktts.voice_dir(args.sound_dir, args.voice)
    if not os.path.isdir(voice_sound_dir):
        info(f"❌ 음성 폴더가 없습니다: {voice_sound_dir}")
        if args.voice != ktts.DEFAULT_VOICE:
            info(f"   --list-voices 로 사용 가능한 목소리를 확인하세요.")
        else:
            info("   --sound-dir 로 경로를 지정하거나 sound.zip 을 풀어주세요.")
        return 1

    if not args.text:
        if args.output:
            parser.error("-o 를 쓰려면 읽을 텍스트도 함께 지정해야 합니다.")
        return interactive(args)

    dry_run = args.show or args.pronounce
    return 0 if speak(" ".join(args.text), args, allow_play=not dry_run) else 1


if __name__ == "__main__":
    sys.exit(main())
