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
    """Red twice; owner must act."""

    exit_code = 5


EXIT_OK = 0
