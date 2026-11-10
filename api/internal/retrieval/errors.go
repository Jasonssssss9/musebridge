package retrieval

// Error carries the safe public classification of a failed retrieval call.
// HTTPStatus is used by question orchestration; this package serves no routes.
type Error struct {
	Code       string `json:"code"`
	Message    string `json:"message"`
	Retryable  bool   `json:"retryable"`
	HTTPStatus int    `json:"-"`
	cause      error
}

func (e *Error) Error() string { return e.Message }
func (e *Error) Unwrap() error { return e.cause }

func invalidResponse(cause error) *Error {
	return &Error{
		Code: "retrieval_invalid_response", HTTPStatus: 502,
		Message: "The evidence service returned an invalid response.", cause: cause,
	}
}

func unavailable(cause error) *Error {
	return &Error{
		Code: "retrieval_unavailable", HTTPStatus: 503, Retryable: true,
		Message: "The evidence service is temporarily unavailable.", cause: cause,
	}
}

func timedOut(cause error) *Error {
	return &Error{
		Code: "retrieval_timeout", HTTPStatus: 504, Retryable: true,
		Message: "The evidence service exceeded its deadline.", cause: cause,
	}
}

func invalidRequest() *Error {
	return &Error{Code: "invalid_request", HTTPStatus: 400, Message: "The retrieval request is invalid."}
}
