import os
from backend.tools.github_tool import GithubTool
tool = GithubTool(
    repo_url="https://github.com/Samarth231204/Ingrecipes.git",
    token="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",
    session_id="testtest123"
)
def custom_docker(arguments, *, input_text=None, secret_env=None, timeout):
    import subprocess
    environment = os.environ.copy()
    environment.update(secret_env or {})
    result = subprocess.run(["docker", *arguments], input=input_text, capture_output=True, text=True, timeout=timeout, check=False, env=environment)
    if result.returncode != 0:
        print("DOCKER STDOUT:", result.stdout)
        print("DOCKER STDERR:", result.stderr)
        raise Exception("Failed")
    return result.stdout
tool._host_docker = custom_docker
try:
    tool._run("clone")
except Exception as e:
    pass
