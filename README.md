# UNICESULAB — Agente de captura

Agente que roda no computador ligado à máquina do laboratório (cabo serial
RS-232/DB9 via adaptador USB-serial), captura tudo que a máquina imprime pela
porta e envia o texto bruto ao servidor UNICESULAB, onde a IA faz o de-para
para os campos do exame e o aluno/professor confere antes de vincular.

## Como funciona

```
máquina ──RS-232──► agente ──POST /api/agent/lab-captures──► UNICESULAB (Laravel)
                      │                                           │
                      └──────────── GET /api/agent/ping ──────────┘ (heartbeat 30s)
```

1. **Pareamento**: na primeira execução o agente pede a URL do servidor e o
   token do agente (gerado pelo professor na tela da máquina, em *Máquinas*).
   O token é validado e salvo no banco local. Dá para "Configurar depois" e
   ajustar a porta serial primeiro: as capturas ficam na fila local e são
   enviadas quando o pareamento acontecer.
2. **Porta serial**: o operador escolhe a porta (COMx no Windows,
   /dev/ttyUSBx no Linux) e os parâmetros. Os defaults são os que funcionam
   com o coagulômetro do laboratório: **57600 baud, 8N1, RTS/CTS ligado**.
   A primeira abertura da porta costuma falhar (driver CH340 no Windows), por
   isso o agente insiste com backoff (0,5 s → 1 s → 2 s → 5 s) até abrir.
3. **Captura**: a máquina imprime cada resultado de uma vez e depois fica em
   silêncio. O agente acumula os bytes e, após o *tempo de silêncio* (3 s por
   padrão) sem receber nada, fecha uma captura. Cada captura guarda o texto
   normalizado, os bytes crus (hex) e um hash SHA-256 do texto, usado pelo
   servidor para ignorar reenvios duplicados.
4. **Envio**: um worker envia as capturas pendentes em ordem. Com o servidor
   fora do ar, aplica backoff progressivo (1s → 5s → 30s) e tenta de novo;
   nada é perdido. Rejeição definitiva do servidor (ex.: validação) marca a
   captura como *Recusada* com o motivo; o botão **Reenviar falhas** devolve
   essas capturas à fila.
5. **Detectar baudrate**: a máquina só transmite quando imprime um resultado,
   então a detecção pede que o operador dispare um teste enquanto o agente
   escuta cada velocidade (115200 → 1200) por alguns segundos e pontua pela
   proporção de bytes ASCII legíveis. Só salva o baudrate quando encontra
   uma velocidade com ≥ 90% de bytes legíveis.

## Persistência local

SQLite (WAL) em `%LOCALAPPDATA%\UnicesulabAgent\` (Windows) ou
`~/.local/share/UnicesulabAgent/` (Linux/macOS). Tabelas:

- `config`: key/value (URL do servidor, token, parâmetros da porta serial)
- `captures`: fila e histórico das capturas (texto, bytes crus, hash, status,
  id no servidor, tentativas, erro)

Logs em `agent.log` no mesmo diretório. O token nunca é logado.

## Desenvolvimento

Dependências gerenciadas com [uv](https://docs.astral.sh/uv/):

```bash
uv sync
uv run main.py
```

A interface é HTML/JS estilizada com **Tailwind CSS** e renderizada com
**pywebview** (WebView2 no Windows; no Linux precisa de WebKitGTK, pacote
`gir1.2-webkit2-4.1` no Debian/Ubuntu). A bandeja do sistema (minimizar e
continuar capturando) só é ativada no Windows; em macOS/Linux fechar a
janela encerra o agente.

No Linux, o usuário precisa estar no grupo `dialout` para abrir a porta:

```bash
sudo usermod -aG dialout "$USER"   # relogar depois
```

### Testes e lint

```bash
uv run pytest
uv run ruff check .
```

Os testes cobrem o framing das capturas (com uma amostra real do
coagulômetro em `tests/fixtures/coagulometro_sample.bin`), a detecção de
baudrate, a fila local, os workers e o cliente HTTP, tudo sem porta física:
a fonte de bytes é uma abstração (`ByteSource`) com implementações fake.

### CSS (Tailwind)

O `agent/ui/web/tailwind.css` é gerado e commitado — só precisa ser
reconstruído ao mexer nas classes do HTML/JS da interface:

```bash
npm install
npm run build:css        # ou npm run watch:css durante o desenvolvimento
```

## Build (Windows)

O PyInstaller não faz cross-compile — o `.exe` precisa ser gerado em um
Windows. O jeito recomendado é o **GitHub Actions**
(`.github/workflows/build-windows.yml`): rode o workflow manualmente na aba
Actions (ou crie uma tag `v*`) e baixe o `UnicesulabAgent.zip` dos
artefatos; tags também anexam o zip à release.

Para gerar localmente numa máquina Windows:

```powershell
uv run --with pyinstaller pyinstaller --noconsole --onefile --name UnicesulabAgent `
  --add-data "agent/ui/web;agent/ui/web" main.py
```

O executável fica em `dist/UnicesulabAgent.exe`. Para iniciar com o
Windows, crie um atalho do executável em `shell:startup` (Win+R → `shell:startup`).

## API do servidor usada pelo agente

Autenticação por `Authorization: Bearer <token>`.

| Método | Rota | Uso |
|---|---|---|
| `GET` | `/api/agent/ping?agent_version=` | Valida o token, devolve agente e máquina vinculada |
| `POST` | `/api/agent/lab-captures` | Envia uma captura (`raw_text`, `raw_hash`, `captured_at`, `agent_version`, `serial_settings`) |

Respostas `401`/`403` indicam token inválido ou agente inativo (o agente
para de enviar até o pareamento ser refeito); `422` é rejeição definitiva da
captura; `429`/`5xx`/timeout contam como servidor indisponível (reenvio
automático).
