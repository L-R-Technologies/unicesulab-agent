# UNICESULAB — Agente de captura

Agente que roda no computador ligado à máquina do laboratório (cabo serial
RS-232/DB9 via adaptador USB-serial), captura tudo que a máquina imprime pela
porta e envia o texto bruto ao servidor UNICESULAB, onde a IA faz o de-para
para os campos do exame e o aluno/professor confere antes de vincular.

## Como funciona

```
máquina A ──RS-232──► agente ──POST /api/agent/lab-captures (token A)──► UNICESULAB (Laravel)
máquina B ──RS-232──►   │    ──POST /api/agent/lab-captures (token B)──►       │
                        └──────────── GET /api/agent/ping (por máquina) ───────┘ (heartbeat 30s)
```

Um único agente, em uma única janela, atende várias máquinas ligadas ao mesmo
computador: cada máquina é uma **conexão** com a própria porta serial, os
próprios parâmetros e o próprio token. O endereço do servidor é um só.

1. **Servidor**: na primeira execução o agente pede só o endereço do UNICESULAB.
   Dá para "Configurar depois": as capturas ficam na fila local e são
   enviadas quando o endereço for informado.
2. **Máquinas**: em **Adicionar máquina** o operador informa um nome, a porta
   (COMx no Windows, /dev/ttyUSBx no Linux), os parâmetros seriais e o token
   gerado pelo professor na tela da máquina, em *Máquinas* no UNICESULAB.
   O token é validado no servidor ao salvar; com o servidor fora do ar, marque
   "salvar sem validar". Os defaults são os que funcionam com o coagulômetro
   do laboratório: **57600 baud, 8N1, RTS/CTS ligado**. A primeira abertura
   da porta costuma falhar (driver CH340 no Windows), por isso cada conexão
   insiste com backoff (0,5 s → 1 s → 2 s → 5 s) até abrir.
3. **Captura**: cada conexão tem o próprio leitor. A máquina imprime cada
   resultado de uma vez e depois fica em silêncio; o agente acumula os bytes
   e, após o *tempo de silêncio* (3 s por padrão) sem receber nada, fecha uma
   captura ligada àquela conexão. Cada captura guarda o texto normalizado, os
   bytes crus (hex) e um hash SHA-256 do texto, usado pelo servidor para
   ignorar reenvios duplicados da mesma máquina.
4. **Envio**: um worker único percorre as conexões ativas e envia as capturas
   pendentes de cada uma com o token dela. O status do servidor é por
   conexão: um token recusado em uma máquina não para as outras. Com o
   servidor fora do ar, aplica backoff progressivo (1s → 5s → 30s) e tenta de
   novo; nada é perdido. Rejeição definitiva (ex.: validação) marca a captura
   como *Recusada* com o motivo; **Reenviar falhas** devolve essas capturas à
   fila.
5. **Detectar baudrate**: a máquina só transmite quando imprime um resultado,
   então a detecção pede que o operador dispare um teste naquela máquina
   enquanto o agente escuta cada velocidade (115200 → 1200) por alguns
   segundos e pontua pela proporção de bytes ASCII legíveis. Só salva o
   baudrate quando encontra uma velocidade com ≥ 90% de bytes legíveis.

Cada card de máquina mostra a porta, o status da porta e do servidor, a
máquina vinculada (vinda do servidor), um console ao vivo e os botões
Editar, Detectar baudrate, Reconectar, Revalidar token e Remover. Desativar
uma máquina (checkbox no formulário) para o leitor e o envio dela sem apagar
nada.

## Persistência local

SQLite (WAL) em `%LOCALAPPDATA%\UnicesulabAgent\` (Windows) ou
`~/.local/share/UnicesulabAgent/` (Linux/macOS). Tabelas:

- `config`: key/value (endereço do servidor)
- `connections`: máquinas conectadas (nome, porta, parâmetros seriais, token,
  ativa)
- `captures`: fila e histórico das capturas (conexão de origem, texto, bytes
  crus, hash, status, id no servidor, tentativas, erro). Remover uma conexão
  apaga as capturas dela.

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
baudrate, a fila local, o gerenciador de conexões, os workers com várias
máquinas e tokens, a bridge da interface e o cliente HTTP, tudo sem porta
física: a fonte de bytes é uma abstração (`ByteSource`) com implementações
fake.

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

Para gerar localmente numa máquina Windows com [uv](https://docs.astral.sh/uv/):

```powershell
uv sync
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

Respostas `401`/`403` indicam token inválido ou agente inativo (a conexão
correspondente para de enviar até o token ser corrigido; as outras seguem); `422` é rejeição definitiva da
captura; `429`/`5xx`/timeout contam como servidor indisponível (reenvio
automático).
