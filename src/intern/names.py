"""The product's names in one place: the command, the package users install, env vars, and the folders it keeps."""

from intern import __version__

NAME = "intern"
TITLE = "Intern"
DISTRIBUTION = "intern-agent"
ENV_PREFIX = "INTERN_"
HOME = "~/.intern"
PROJECT_DIR = ".intern"
RESOURCES_DIR = ".intern-resources"
USER_AGENT = f"{NAME}/{__version__}"
# Sent with every model and System 1 request, so Drex and providers can tell Intern's traffic apart.
# `X-Title` is OpenRouter's app attribution.
CLIENT_HEADERS = {"User-Agent": USER_AGENT, "X-Nace-Client": NAME, "X-Title": TITLE}
