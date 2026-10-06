const messages = require("./messages.json");
function failureMessage(code) {
    return Object.hasOwn(messages, code) ? messages[code] : messages.REQUEST_FAILED;
}
function knownFailure(error) {
    return error instanceof Error && Object.values(messages).includes(error.message)
        ? error.message
        : messages.REQUEST_FAILED;
}
module.exports = { failureMessage, knownFailure };
