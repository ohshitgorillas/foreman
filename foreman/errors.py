"""The one exception type per failure kind and the one exit-code table."""


class ForemanError(Exception):
    exit_code: int = 1
    kind: str = "infrastructure"  # a member of events.STOP_KINDS


class ConfigError(ForemanError):
    """Bad or missing foreman.toml."""

    exit_code = 2


class BackendError(ForemanError):
    """Agent could not start or vanished."""

    exit_code = 4


class RulingNeeded(ForemanError):
    """Refusal, overload, missed step check, unnamed group path or no change; Void rules."""

    exit_code = 6
    kind = "steering"


class FindingsToRule(ForemanError):
    """Reviewers filed findings; Void upholds or declines each."""

    exit_code = 7
    kind = "findings"


class WrongTest(ForemanError):
    """Spec defect, undetermined judgment or exhausted juror rounds; owner acts."""

    exit_code = 8
    kind = "owner"


EXIT_OK = 0
