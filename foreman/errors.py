"""The one exception type per failure kind and the one exit-code table."""


class ForemanError(Exception):
    exit_code: int = 1


class ConfigError(ForemanError):
    """Bad or missing foreman.toml."""

    exit_code = 2


class OverloadedStep(ForemanError):
    """Step file fails the lint."""

    exit_code = 3


class BackendError(ForemanError):
    """Agent could not start or vanished."""

    exit_code = 4


class Escalated(ForemanError):
    """Red twice; Void, the dispatcher, acts."""

    exit_code = 5


class RulingNeeded(ForemanError):
    """Refusal, overload, missed step check, unnamed group path or no change; Void rules."""

    exit_code = 6


class FindingsToRule(ForemanError):
    """Reviewers filed findings; Void upholds or declines each."""

    exit_code = 7


class WrongTest(ForemanError):
    """A builder reported TEST WRONG; Void, the dispatcher, rules and runs `retest`."""

    exit_code = 8


EXIT_OK = 0
