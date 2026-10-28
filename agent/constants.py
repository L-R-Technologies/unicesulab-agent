"""Constantes de configuração do agente."""

APP_NAME = "UnicesulabAgent"

# Porta serial (defaults validados com o coagulômetro do laboratório)
DEFAULT_BAUDRATE = 57600
DEFAULT_BYTESIZE = 8
DEFAULT_PARITY = "N"
DEFAULT_STOPBITS = 1
DEFAULT_RTSCTS = True
SERIAL_READ_TIMEOUT_SECONDS = 1.0
# A 1ª abertura da porta costuma falhar (CH340 no Windows); insiste com backoff
SERIAL_OPEN_BACKOFF_STEPS_SECONDS = (0.5, 1.0, 2.0, 5.0)
SERIAL_NOT_CONFIGURED_WAIT_SECONDS = 1.0

# Framing: a máquina imprime um resultado de uma vez; silêncio = fim da captura
DEFAULT_IDLE_TIMEOUT_SECONDS = 3.0
MIN_IDLE_TIMEOUT_SECONDS = 0.5
MAX_IDLE_TIMEOUT_SECONDS = 60.0
MAX_CAPTURE_BYTES = 64 * 1024

# Detecção de baudrate (só funciona enquanto a máquina transmite)
BAUDRATE_CANDIDATES = (115200, 57600, 38400, 19200, 9600, 4800, 2400, 1200)
DETECT_LISTEN_SECONDS = 4.0
DETECT_PRINTABLE_THRESHOLD = 0.9

# Comunicação com o servidor
API_TIMEOUT_SECONDS = 5.0
UPLOAD_INTERVAL_SECONDS = 1.0
UPLOAD_BACKOFF_STEPS_SECONDS = (1.0, 5.0, 30.0)
HEARTBEAT_INTERVAL_SECONDS = 30.0
AUTH_ERROR_RETRY_SECONDS = 30.0

# Interface
LIVE_TEXT_MAX_CHARS = 16_000
RECENT_CAPTURES_LIMIT = 30

# Persistência local
DB_FILENAME = "unicesulab_agent.db"

# Chaves da tabela config
CONFIG_SERVER_URL = "server_url"
CONFIG_AGENT_TOKEN = "agent_token"
CONFIG_PAIRING_SKIPPED = "pairing_skipped"
CONFIG_SERIAL_PORT = "serial_port"
CONFIG_SERIAL_BAUDRATE = "serial_baudrate"
CONFIG_SERIAL_BYTESIZE = "serial_bytesize"
CONFIG_SERIAL_PARITY = "serial_parity"
CONFIG_SERIAL_STOPBITS = "serial_stopbits"
CONFIG_SERIAL_RTSCTS = "serial_rtscts"
CONFIG_SERIAL_IDLE_TIMEOUT = "serial_idle_timeout"
