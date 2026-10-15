from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_run_monitor_only_unregisters_after_validated_completion() -> None:
    script = (PROJECT_ROOT / "scripts" / "monitor-run.ps1").read_text(encoding="utf-8")
    assert "$Manifest.status -eq 'success'" in script
    assert "$Checkpoint.current_stage -eq 'complete'" in script
    assert "-not $Checkpoint.can_resume" in script
    assert "$SuccessCount -ge $ExpectedItems" in script
    assert "if ($Complete -and -not $DryRun)" in script
    assert "Unregister-ScheduledTask -TaskName $TaskName" in script
