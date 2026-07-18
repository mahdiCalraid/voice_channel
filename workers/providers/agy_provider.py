import os
import subprocess
import shutil
from workers.providers.base_provider import BaseProvider

class AgyProvider(BaseProvider):
    def execute(self, model: str, prompt: str, env_creds: dict, agent: str = None) -> str:
        cmd_path = shutil.which("agy")
        if not cmd_path:
            for fallback in ("/Users/ed/.local/bin/agy", "/Users/ed/.antigravity/antigravity/bin/agy"):
                if os.path.exists(fallback):
                    cmd_path = fallback
                    break
                    
        if not cmd_path:
            raise FileNotFoundError("The 'agy' CLI executable could not be found in PATH or standard user directories.")
            
        # Build env dictionary, passing credentials
        process_env = os.environ.copy()
        for k, v in env_creds.items():
            if v:
                process_env[k] = v
                
        # Build command arguments
        cmd = [
            cmd_path,
            "--dangerously-skip-permissions",
            "--print",
            prompt
        ]
        
        # If agent is specified, pass --agent
        if agent:
            cmd.extend(["--agent", agent])
            
        # If model override is specified, pass --model
        if model:
            cmd.extend(["--model", model])
            
        result = subprocess.run(
            cmd,
            env=process_env,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL
        )
        
        if result.returncode != 0:
            error_msg = result.stderr.strip() or f"Process exited with code {result.returncode}"
            raise RuntimeError(f"agy CLI execution failed: {error_msg}")
            
        return result.stdout.strip()
