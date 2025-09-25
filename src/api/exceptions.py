from aiohttp.web_exceptions import HTTPClientError

class APIBadRequest(HTTPClientError):
    status_code = 400
    reason = 'Bad Request'

class APIForbidden(HTTPClientError):
    status_code = 403
    reason = 'Forbidden'

class APINotFound(HTTPClientError):
    status_code = 404
    reason = 'Not Found'

class APIConflict(HTTPClientError):
    status_code = 409
    reason = 'Conflict'

class APIUnprocessableEntity(HTTPClientError):
    status_code = 422
    reason = 'Unprocessable Entity'

class APIInternalServerError(HTTPClientError):
    status_code = 500
    reason = 'Internal Server Error'
