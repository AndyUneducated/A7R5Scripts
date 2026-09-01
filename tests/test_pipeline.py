from vclip.pipeline import ConsoleReporter, NullReporter
from vclip.split import plan_duration
from conftest import make_caps, make_opts, make_sdr_info


class RecordingReporter:
    """收集上报事件的测试 Reporter（不打印）。"""

    def __init__(self):
        self.commands = []
        self.notes = []
        self.started = []
        self.done = []

    def command(self, cmd, index=1, total=1):
        self.commands.append((tuple(cmd), index, total))

    def segment_start(self, index, total):
        self.started.append((index, total))

    def segment_done(self, index, total):
        self.done.append((index, total))

    def note(self, message):
        self.notes.append(message)


def test_execute_reports_commands_without_printing(capsys):
    # 执行层不再自己 print：dry-run 只通过 reporter 上报命令，stdout 应为空
    plan = plan_duration(
        make_sdr_info(duration=120), 30, make_caps(), make_opts(), transcode=False
    )
    rec = RecordingReporter()
    files = plan.execute(dry_run=True, reporter=rec)
    assert files == []
    assert len(rec.commands) == 1
    assert capsys.readouterr().out == ""


def test_execute_defaults_to_silent_reporter(capsys):
    plan = plan_duration(
        make_sdr_info(duration=120), 30, make_caps(), make_opts(), transcode=False
    )
    plan.execute(dry_run=True)  # 无 reporter → NullReporter
    assert capsys.readouterr().out == ""


def test_null_reporter_is_silent(capsys):
    r = NullReporter()
    r.command(["ffmpeg"], 1, 3)
    r.segment_start(1, 3)
    r.segment_done(1, 3)
    r.note("hi")
    assert capsys.readouterr().out == ""


def test_console_reporter_prints_command(capsys):
    ConsoleReporter().command(["ffmpeg", "-i", "a b.mp4"], 2, 5)
    out = capsys.readouterr().out
    assert "[2/5]" in out
    assert "'a b.mp4'" in out  # 含空格路径被 shlex 引用
