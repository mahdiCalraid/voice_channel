import os
from openai import OpenAI
from workers.providers.base_provider import BaseProvider

class OpenAIProvider(BaseProvider):
    def execute(self, model: str, prompt: str, env_creds: dict, agent: str = None) -> str:
        api_key = env_creds.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OpenAI API key (OPENAI_API_KEY) is missing.")
            
        client = OpenAI(api_key=api_key)
        
        completion = client.chat.completions.create(
            model=model or "gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a concise channel narrator summarizing agent workspaces."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=400,
            temperature=0.3
        )
        return completion.choices[0].message.content.strip()
