set -eu

mkfifo /tmp/nginx-errors
awk '
{
    message = $0
    level = "ERROR"
    if (match(message, /\[(debug|info|notice|warn|error|crit|alert|emerg)\]/)) {
        level = toupper(substr(message, RSTART + 1, RLENGTH - 2))
    }
    gsub(/\\/, "\\\\\\\\", message)
    gsub(/"/, "\\\"", message)
    gsub(/\t/, "\\t", message)
    gsub(/\r/, "\\r", message)
    printf "{\"level\":\"%s\",\"logger\":\"nginx.error\",\"message\":\"%s\"}\n", level, message
    fflush()
}
' < /tmp/nginx-errors &
exec nginx -g 'daemon off;'
