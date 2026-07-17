import abc

class BaseProvider(abc.ABC):
    @abc.abstractmethod
    def execute(self, model: str, prompt: str, env_creds: dict, agent: str = None) -> str:
        """
        Execute the model prompt with the given model name and credentials.
        Returns the output text response.
        """
        pass
