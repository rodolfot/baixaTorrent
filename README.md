# BaixaTorrent

Ferramenta para mapear e baixar arquivos `.torrent` e links `magnet:` de um site inteiro, com interface gráfica moderna e suporte a retomada de sessão.

---

## Funcionalidades

- **Mapeamento completo do site** — varre todas as páginas do domínio antes de iniciar os downloads
- **50 workers paralelos** — mapeamento rápido com requisições simultâneas
- **Download automático** — baixa arquivos `.torrent` diretos via HTTP e segue cadeias de redirecionamento com anúncios usando Playwright
- **Coleta de links magnet** — lista e copia links `magnet:` encontrados
- **Sessão persistida** — salva o estado a cada 30 páginas; retome de onde parou a qualquer momento
- **Evidências** — screenshots de páginas e relatório JSON completo
- **Interface gráfica** — janela amigável com log em tempo real e lista de arquivos encontrados

---

## Requisitos

- Python 3.12 ou superior
- Windows 10/11

---

## Instalação

**1. Clone o repositório**

```bash
git clone https://github.com/rodolfot/baixaTorrent.git
cd baixaTorrent
```

**2. Instale as dependências Python**

```bash
pip install -r requirements.txt
```

**3. Instale o browser do Playwright**

```bash
python -m playwright install chromium
```

---

## Como usar

### Interface gráfica (recomendado)

```bash
python gui.py
```

A janela abre com os seguintes elementos:

```
┌─────────────────────────────────────────────────────┐
│  ⬇ BaixaTorrent                      ● Aguardando  │
├─────────────────────────────────────────────────────┤
│  URL: [___________________________] [Iniciar][Reset]│
├─────────────────────────────────────────────────────┤
│  Páginas: 0  Torrents: 0  Magnets: 0  Na fila: 0   │
├────────────────────┬────────────────────────────────┤
│  Atividade (log)   │  Arquivos Encontrados          │
│                    │                                │
│  [log em tempo     │  📥 arquivo.torrent  [Abrir]  │
│   real...]         │  🧲 magnet:?xt=...   [Copiar] │
│                    │                                │
└────────────────────┴────────────────────────────────┘
```

**Passo a passo:**

1. Cole a URL raiz do site no campo **URL** (ex: `https://www.exemplo.com`)
2. Clique em **Iniciar** ou pressione `Enter`
3. Se houver uma sessão salva para aquele site, um diálogo perguntará se deseja **retomar de onde parou**
4. Acompanhe o progresso no painel **Atividade** (esquerda)
5. Os arquivos encontrados aparecem no painel direito conforme são baixados
6. Ao concluir, clique em **📂 Abrir pasta** para acessar os arquivos baixados

**Botões:**

| Botão | Função |
|---|---|
| **Iniciar** | Inicia o rastreamento |
| **Parar** | Interrompe e salva o estado para retomada futura |
| **Resetar** | Apaga o estado salvo do site atual |
| **📂 Abrir pasta** | Abre a pasta `evidencias/` no Explorer |
| **Copiar** (magnet) | Copia o link magnet para a área de transferência |
| **Abrir** (torrent) | Abre a pasta do arquivo `.torrent` baixado |

---

### Linha de comando (alternativo)

```bash
python baixa_torrent.py https://www.exemplo.com
```

Ou sem argumento (o programa pedirá a URL):

```bash
python baixa_torrent.py
```

---

## Como funciona internamente

O programa opera em **2 fases sequenciais**. A fase 1 precisa terminar completamente antes da fase 2 começar.

| Fase | Nome | Tecnologia | O que faz |
|---|---|---|---|
| **1** | Mapeamento | `requests` + 50 workers | Varre todo o site coletando links — sem baixar nada |
| **2** | Download | `requests` + Playwright | Baixa os arquivos encontrados na fase 1 |

### Fase 1 — Mapeamento

Usa `requests` + `BeautifulSoup` com **50 workers em paralelo** para varrer todo o site rapidamente, **sem baixar nenhum arquivo ainda**.

- Inicia na URL raiz e segue todos os links do mesmo domínio (BFS — busca em largura)
- Coleta URLs de arquivos `.torrent` e links `magnet:` em cada página visitada
- Ignora arquivos de mídia, CSS, JS e domínios de anúncios conhecidos
- Salva o estado a cada 30 páginas em `evidencias/estados/<dominio>.json`
- A barra superior exibe em tempo real:
  - **Páginas** — total de páginas já visitadas
  - **Na fila** — quantas páginas ainda aguardam para ser visitadas (trabalho pendente). Quando chega a 0, o mapeamento está completo
  - **Torrents / Magnets** — totais encontrados até o momento

### Fase 2 — Download

Somente após o mapeamento completo do site, inicia os downloads:

1. **Downloads diretos** — baixa todos os `.torrent` encontrados via `requests` em paralelo
2. **Playwright** — para páginas que exigem interação (anúncios, redirecionamentos, contadores regressivos):
   - Navega até a página
   - Aguarda contadores regressivos
   - Clica em botões "Skip / Pular / Continuar"
   - Fecha abas abertas por anúncios
   - Segue cadeias de redirecionamento (até 5 saltos)
   - Tira screenshot de cada página como evidência

---

## Estrutura de saída

Todos os arquivos são salvos na pasta `evidencias/`:

```
evidencias/
├── torrents/               # Arquivos .torrent baixados
│   ├── nome-do-arquivo.torrent
│   └── ...
├── screenshots/            # Screenshots das páginas visitadas
│   ├── 20240418_143022_0001.png
│   └── ...
├── estados/                # Estado de sessão (para retomada)
│   └── www_exemplo_com.json
├── mapa_20240418_143022.json      # Mapa completo do site
└── relatorio_20240418_143022.json # Relatório final com todos os dados
```

### Estrutura do relatório JSON

```json
{
  "session": "20240418_143022",
  "start_url": "https://www.exemplo.com",
  "pages": 1247,
  "torrents": 83,
  "magnets": 12,
  "site_map": {
    "https://www.exemplo.com/filme/1": {
      "torrents": ["https://.../.torrent"],
      "magnets": ["magnet:?xt=..."],
      "links": ["https://..."]
    }
  },
  "downloaded": [
    "evidencias/torrents/nome.torrent"
  ]
}
```

---

## Retomada de sessão

Se o processo for interrompido (botão Parar, fechamento da janela, queda de energia), o estado é preservado automaticamente.

Para retomar:

1. Abra o programa novamente com `python gui.py`
2. Digite a mesma URL na caixa
3. Clique **Iniciar**
4. Selecione **Sim** na caixa de diálogo "Deseja retomar de onde parou?"

O programa reconstituirá a fila a partir das páginas já mapeadas e continuará sem revisitar o que já foi processado.

Para iniciar do zero, clique em **Resetar** antes de iniciar.

---

## Configurações avançadas

As constantes no topo de `crawler.py` permitem ajustar o comportamento:

| Constante | Padrão | Descrição |
|---|---|---|
| `WORKERS` | `50` | Número de workers paralelos |
| `SAVE_INTERVAL` | `30` | Salvar estado a cada N páginas |
| `MAX_PAGES` | `5000` | Limite máximo de páginas a visitar |
| `MAX_HOPS` | `5` | Máximo de redirecionamentos a seguir |

---

## Observações

- **Rate limiting** — Com 50 workers, alguns sites podem bloquear o IP temporariamente. Se isso ocorrer, reduza `WORKERS` para 10–20 em `crawler.py`
- **Sites com JavaScript** — A fase 1 usa `requests` (sem JS). Páginas que dependem de JS para renderizar links de torrent são investigadas automaticamente na fase 2 com Playwright
- **Anúncios** — Domínios de anúncios conhecidos são bloqueados automaticamente durante a navegação com Playwright
- **Pasta `evidencias/`** — Não é versionada no Git (está no `.gitignore`). Faça backup manualmente se necessário

---

## Dependências

| Pacote | Uso |
|---|---|
| `playwright` | Navegação em páginas com JS e anúncios |
| `beautifulsoup4` | Parsing de HTML |
| `requests` | Requisições HTTP para mapeamento rápido |
| `customtkinter` | Interface gráfica moderna |
| `Pillow` | Suporte a imagens na interface |
