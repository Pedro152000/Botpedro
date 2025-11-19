#!/usr/bin/env python3
# bot.py - FlashScore (versão estável) -> envia sinais PREMIUM para Telegram
import time
import requests
from bs4 import BeautifulSoup
import telebot
import logging
import re

# ----------------------------
# CONFIGURAÇÃO (já com seus dados)
# ----------------------------
BOT_TOKEN = "8279285665:AAGRi2DQg3Mu3gJmZrKdub_0oHybZKQOSA0"
CHAT_ID = "959511946"

# Intervalo entre varreduras (segundos) — para FlashScore estável use 20-30s
POLL_INTERVAL = 25

# Flashscore URLs
FS_HOME = "https://www.flashscore.com/football/"
FS_BASE = "https://www.flashscore.com"

# Inicializa bot Telegram
bot = telebot.TeleBot(BOT_TOKEN)

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

# Cabeçalhos que parecem um navegador real
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7"
}

# Evita duplicar sinais (memória simples)
sent_signals = set()
last_scores = {}  # partida_id -> "1-0"

# ----------------------------
# UTILITÁRIOS DE EXTRAÇÃO
# ----------------------------
def fetch_html(url, timeout=12):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r.text
    except Exception as e:
        logging.warning("Falha ao buscar %s : %s", url, e)
        return ""

def find_live_matches_from_home(html):
    """Procura links /match/ na página principal e extrai texto relevante."""
    soup = BeautifulSoup(html, "lxml")
    matches = []

    # procura por anchors com href contendo '/match/'
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/match/"):
            raw = a.get_text(" ", strip=True)
            # filtra textos curtos/irrelevantes
            if raw and len(raw) > 8:
                matches.append({"link": href, "raw": raw})
    # dedupe por raw text
    unique = []
    seen = set()
    for m in matches:
        if m["raw"] not in seen:
            unique.append(m)
            seen.add(m["raw"])
    return unique

def parse_match_summary_from_raw(raw):
    """
    Tenta extrair times, minuto e placar de um texto bruto.
    Isso é heurístico — serve como fallback.
    """
    # Exemplo de raw: "Chelsea 1 - 0 Arsenal 67'"
    # Remove múltiplos espaços
    text = re.sub(r'\s+', ' ', raw).strip()
    # tenta localizar minuto (número seguido de ')
    minute = None
    mm = re.search(r"(\d{1,3})'", text)
    if mm:
        minute = mm.group(1)
    # tenta extrair placar X-Y
    score = None
    sc = re.search(r"(\d+)\s*[-–]\s*(\d+)", text)
    if sc:
        score = f"{sc.group(1)}-{sc.group(2)}"
    # times: pegar primeira parte antes do placar e depois
    home = "Unknown"
    away = "Unknown"
    if score:
        parts = text.split(score)
        if len(parts) >= 2:
            left = parts[0].strip()
            right = parts[1].strip()
            # left tem home possivelmente com nome
            left_names = left.split()
            right_names = right.split()
            # heurística simples
            home = " ".join(left_names[:-0]) if left else left
            # away = first few words of right until minute or end
            away = right.split()[0] if right else "Away"
    return {"minute": minute or "N/A", "score": score or "0-0", "home": home, "away": away}

def fetch_match_stats(match_href):
    """
    Tenta abrir a página do jogo e extrair estatísticas (corners, attacks, shots).
    Retorna dicionário com chaves: 'corners', 'attacks', 'shots' (inteiros ou None).
    """
    url = FS_BASE + match_href
    html = fetch_html(url)
    if not html:
        return None
    soup = BeautifulSoup(html, "lxml")

    stats = {"corners": None, "attacks": None, "shots": None}

    # Flashscore tem blocos com classe "statValue" / labels; vamos procurar por palavras-chave
    try:
        # procura por blocos de estatística textual
        text = soup.get_text(" ", strip=True).lower()
        # corners
        m = re.search(r'escanteios\s*(\d+)\s*-\s*(\d+)', text)
        if m:
            stats["corners"] = int(m.group(1)) + int(m.group(2))
        else:
            # english
            m = re.search(r'corners\s*(\d+)\s*-\s*(\d+)', text)
            if m:
                stats["corners"] = int(m.group(1)) + int(m.group(2))
        # shots on target or shots
        m2 = re.search(r'(shots on target|shots)\s*(\d+)\s*-\s*(\d+)', text)
        if m2:
            stats["shots"] = int(m2.group(2)) + int(m2.group(3))
        # attacks / dangerous attacks
        m3 = re.search(r'(attacks|dangerous attacks|ataques perigosos)\s*(\d+)\s*-\s*(\d+)', text)
        if m3:
            stats["attacks"] = int(m3.group(2)) + int(m3.group(3))
    except Exception as e:
        logging.debug("Erro parsing stats: %s", e)
    return stats

# ----------------------------
# LÓGICA DE SINAIS (PREMIUM FORMAT)
# ----------------------------
def build_premium_message(match_info, analysis, tipo):
    home = match_info.get("home", "Home")
    away = match_info.get("away", "Away")
    league = match_info.get("league", "Competição")
    minute = match_info.get("minute", "N/A")
    score = match_info.get("score", "0-0")

    if tipo == "GOL":
        text = (
            "📊 ANÁLISE AO VIVO – PADRÃO DETECTADO!\n\n"
            f"🏆 Liga: {league}\n"
            f"🔵 Jogo: {home} vs {away}\n"
            f"⏱ Minuto: {minute}'\n\n"
            f"🔥 Intensidade ofensiva: {analysis.get('intensity','N/A')}%\n"
            f"⚽ Finalizações no alvo (estimado): {analysis.get('shots','N/A')}\n"
            f"🚩 Escanteios recentes: {analysis.get('corners','N/A')}\n"
            f"🟦 Ataques perigosos (estimado): {analysis.get('attacks','N/A')}\n\n"
            f"🎯 Sugestão:\n➡️ Gol a favor de {home if analysis.get('favor_home') else 'ambos'}\n"
            f"Probabilidade estimada: {analysis.get('prob', 75)}%\n\n"
            "📡 Bot Pedro — Sistema Automático"
        )
    else:  # ESCANTEIO
        text = (
            "📊 ANÁLISE AO VIVO – PADRÃO DE ESCANTEIO!\n\n"
            f"🏆 Liga: {league}\n"
            f"🔵 Jogo: {home} vs {away}\n"
            f"⏱ Minuto: {minute}'\n\n"
            f"📊 Escanteios totais: {analysis.get('corners','N/A')}\n"
            f"🟦 Ataques recentes: {analysis.get('attacks','N/A')}\n"
            f"🔥 Probabilidade estimada: {analysis.get('prob', 80)}%\n\n"
            f"➡️ Entrada sugerida: Próximo escanteio\n\n"
            "📡 Bot Pedro — Sistema Automático"
        )
    return text

def analyze_and_send(match):
    """
    match: dict with keys 'link' and 'raw' at least.
    """
    # basic parse from raw for fallback
    parsed = parse_match_summary_from_raw(match.get("raw",""))
    match_href = match.get("link")
    match_info = {"home": parsed.get("home"), "away": parsed.get("away"),
                  "minute": parsed.get("minute"), "score": parsed.get("score"),
                  "league": "Desconhecida"}

    # try to fetch match detail for better stats
    stats = fetch_match_stats(match_href) if match_href else None

    # build analysis heuristics
    analysis = {"corners": None, "attacks": None, "shots": None, "prob": None, "intensity": None, "favor_home": True}

    if stats:
        analysis["corners"] = stats.get("corners")
        analysis["attacks"] = stats.get("attacks")
        analysis["shots"] = stats.get("shots")
        # simple heuristics for intensity and probability
        intensity = 0
        if analysis["attacks"]:
            intensity += min(100, int(analysis["attacks"] / 2))
        if analysis["shots"]:
            intensity += min(100, int(analysis["shots"] * 3))
        analysis["intensity"] = min(100, intensity)
        # favor home if more attacks on home side? fallback random-like
        analysis["favor_home"] = True
        # prob estimada
        analysis["prob"] = min(95, 40 + (analysis["intensity"] // 2))
    else:
        # fallback heuristics from raw/score
        score = parsed.get("score","0-0")
        try:
            s1, s2 = map(int, score.split("-"))
        except:
            s1, s2 = 0, 0
        total = s1 + s2
        analysis["shots"] = None
        analysis["attacks"] = None
        analysis["corners"] = None
        analysis["intensity"] = 40 + min(40, total*10)
        analysis["prob"] = 60 + min(30, total*10)
        analysis["favor_home"] = True

    # Decide signals:
    # GOL: if placar mudou since last_scores OR intensity high + minute in range
    key_id = match.get("link", match.get("raw"))
    current_score = parsed.get("score","0-0")
    prev = last_scores.get(key_id)
    last_scores[key_id] = current_score

    sent_any = False

    # detect goal by change
    if prev and prev != current_score:
        # got a score change -> send GOL
        unique = f"{key_id}-GOL-{current_score}"
        if unique not in sent_signals:
            msg = build_premium_message(match_info, analysis, "GOL")
            try:
                bot.send_message(CHAT_ID, msg)
                logging.info("Enviado sinal GOL: %s", key_id)
            except Exception as e:
                logging.error("Erro ao enviar GOL: %s", e)
            sent_signals.add(unique)
            sent_any = True

    # detect escanteio by corners heuristic (if known)
    if analysis.get("corners") is not None:
        # If many corners recently or rising, send escanteio signal
        if analysis["corners"] >= 8 and analysis.get("intensity",0) >= 50:
            unique = f"{key_id}-ESC-{analysis['corners']}"
            if unique not in sent_signals:
                msg = build_premium_message(match_info, analysis, "ESCANTEIO")
                try:
                    bot.send_message(CHAT_ID, msg)
                    logging.info("Enviado sinal ESCANTEIO: %s", key_id)
                except Exception as e:
                    logging.error("Erro ao enviar ESC: %s", e)
                sent_signals.add(unique)
                sent_any = True

    # heuristic: if intensity very high and minute between 15-85, maybe send Gol suggestion
    if not sent_any and analysis.get("intensity",0) >= 85 and 15 <= int(match_info.get("minute") if str(match_info.get("minute")).isdigit() else 50) <= 85:
        unique = f"{key_id}-INT-{analysis['intensity']}"
        if unique not in sent_signals:
            msg = build_premium_message(match_info, analysis, "GOL")
            try:
                bot.send_message(CHAT_ID, msg)
                logging.info("Enviado sinal de INTENSIDADE: %s", key_id)
            except Exception as e:
                logging.error("Erro ao enviar INT: %s", e)
            sent_signals.add(unique)

# ----------------------------
# LOOP PRINCIPAL
# ----------------------------
def run():
    # notify start (try safe)
    try:
        bot.send_message(CHAT_ID, "🤖 Bot FlashScore (estável) iniciado — monitorando partidas...")
    except Exception:
        logging.info("Não foi possível enviar mensagem de início — verifique permissões do bot.")

    while True:
        try:
            html = fetch_html(FS_HOME)
            if not html:
                logging.info("Nenhum HTML recebido do FlashScore.")
                time.sleep(POLL_INTERVAL)
                continue

            candidates = find_live_matches_from_home(html)
            logging.info("Encontrados %d candidatos na página principal.", len(candidates))

            # Para cada candidato, analisar e possivelmente enviar sinal
            for c in candidates:
                # filtrar entradas muito curtas
                if len(c.get("raw","")) < 10:
                    continue
                try:
                    analyze_and_send(c)
                except Exception as e:
                    logging.exception("Erro analisando partida: %s", e)

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            logging.info("Interrompido pelo usuário.")
            break
        except Exception as e:
            logging.exception("Erro no loop principal: %s", e)
            time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    run()