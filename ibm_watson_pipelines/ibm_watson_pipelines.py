from ibm_cloud_sdk_core import BaseService, DetailedResponse, ApiException
from ibm_cloud_sdk_core.authenticators import CloudPakForDataAuthenticator

'''
class WatsonPipelines(BaseService):
    @classmethod
    def from_token(cls, bearer_token: str = None, *, service_name: str = None, url: str = None) -> 'WatsonPipelines':
        """
        Return a new Watson Pipelines client for the specified bearer token.
        """
        return cls(
            bearer_token=bearer_token,
            auth_method=AuthMethod.BEARER_TOKEN,
            service_name=service_name,
            url=url,
        )   

    def __init__(
        self,
        apikey: str = None,
        *,
        bearer_token: str = None,
        service_name: str = None,
        url: str = None,
        auth_method: AuthMethod = None,
    ):

        url = self._get_cpd_api_url(url)
        validate_type(url, "url", str)

        authenticator, is_public = self._get_authenticator(
            auth_method,
            apikey=apikey,
            bearer_token=bearer_token,
            url=url,
        )

        if service_name is None:
            service_name = self.DEFAULT_SERVICE_NAME

        super().__init__(
            service_url=url,
            authenticator=authenticator,
            disable_ssl_verification=not is_public,
        )
        self.authenticator = authenticator
        self.configure_service(service_name)
        self.is_public = is_public

'''


