#!/usr/bin/env python3
# bot.py - FlashScore (estável) com contador de GREEN/RED
# Regras:
# - Envia sinal (GOL / ESCANTEIO) usando heurísticas
# - Registra sinal pendente em pending_signals.json
# - Marca GREEN se o evento ocorrer após o sinal
# - Marca RED no HT (intervalo) ou FT (final) se evento não ocorreu
# - Persistência em stats.json (greens / reds) e pending_signals.json

import time
import requests
from bs4 import BeautifulSoup
import telebot
import logging
import re
import json
import os
from datetime import datetime

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

# Filenames for persistence
STATS_FILE = "stats.json"
PENDING_FILE = "pending_signals.json"

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

# In-memory caches
sent_signals = set()     # evita duplicações imediatas
last_scores = {}         # key -> score string
# pending_signals: dict key -> { type, match_key, sign_time, sign_score, sign_corners, raw, resolved: None/ 'GREEN'/'RED' }
pending_signals = {}

# ----------------------------
# Persistence helpers
# ----------------------------
def load_json_file(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logging.warning("Erro carregando %s: %s", path, e)
    return default

def save_json_file(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error("Erro salvando %s: %s", path, e)

# stats: {'greens': int, 'reds': int}
stats = load_json_file(STATS_FILE, {"greens": 0, "reds": 0})
pending_signals = load_json_file(PENDING_FILE, {})

# ----------------------------
# UTILITÁRIOS
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
    """Extrai minuto e placar de um texto bruto (heurístico)."""
    text = re.sub(r'\s+', ' ', raw).strip()
    minute = None
    status = None
    mm = re.search(r"(\d{1,3})'", text)
    if mm:
        minute = mm.group(1)
    # detect HT/FT keywords
    if re.search(r'\b(ht|half-time|intervalo)\b', text, re.I):
        status = "HT"
    if re.search(r'\b(ft|full-time|final)\b', text, re.I):
        status = "FT"
    # score
    score = None
    sc = re.search(r"(\d+)\s*[-–]\s*(\d+)", text)
    if sc:
        score = f"{sc.group(1)}-{sc.group(2)}"
    # try to get teams around the score
    home = "Home"
    away = "Away"
    if score:
        parts = text.split(score)
        if len(parts) >= 2:
            left = parts[0].strip()
            right = parts[1].strip()
            home = left
            away = right.split()[0] if right else "Away"
    return {"minute": minute or "N/A", "score": score or "0-0", "home": home, "away": away, "status": status or ""}

def fetch_match_stats(match_href):
    """Tenta extrair estatísticas da página do jogo (corners, attacks, shots)."""
    url = FS_BASE + match_href
    html = fetch_html(url)
    if not html:
        return None
    soup = BeautifulSoup(html, "lxml")
    stats = {"corners": None, "attacks": None, "shots": None}
    try:
        text = soup.get_text(" ", strip=True).lower()
        # corners
        m = re.search(r'escanteios\s*(\d+)\s*[-–]\s*(\d+)', text)
        if m:
            stats["corners"] = int(m.group(1)) + int(m.group(2))
        else:
            m = re.search(r'corners\s*(\d+)\s*[-–]\s*(\d+)', text)
            if m:
                stats["corners"] = int(m.group(1)) + int(m.group(2))
        # shots
        m2 = re.search(r'(shots on target|shots)\s*(\d+)\s*[-–]\s*(\d+)', text)
        if m2:
            stats["shots"] = int(m2.group(2)) + int(m2.group(3))
        # attacks
        m3 = re.search(r'(attacks|dangerous attacks|ataques perigosos)\s*(\d+)\s*[-–]\s*(\d+)', text)
        if m3:
            stats["attacks"] = int(m3.group(2)) + int(m3.group(3))
    except Exception as e:
        logging.debug("Erro parsing stats: %s", e)
    return stats

# ----------------------------
# PERSISTÊNCIA DE SINAL (CRIAR / RESOLVER)
# ----------------------------
def add_pending_signal(key, kind, match_key, sign_score, sign_corners, raw):
    sign = {
        "kind": kind,                 # "GOL" ou "ESCANTEIO"
        "match_key": match_key,       # link ou raw identifier
        "sign_time": datetime.utcnow().isoformat(),
        "sign_score": sign_score,     # "1-0"
        "sign_corners": sign_corners, # int or None
        "raw": raw,
        "resolved": None,             # "GREEN" ou "RED"
        "resolved_time": None
    }
    pending_signals[key] = sign
    save_json_file(PENDING_FILE, pending_signals)

def resolve_pending_signal(key, result):
    """result: 'GREEN' or 'RED'"""
    entry = pending_signals.get(key)
    if not entry:
        return
    entry["resolved"] = result
    entry["resolved_time"] = datetime.utcnow().isoformat()
    # atualizar stats
    if result == "GREEN":
        stats["greens"] = stats.get("greens", 0) + 1
    elif result == "RED":
        stats["reds"] = stats.get("reds", 0) + 1
    # persist
    save_json_file(STATS_FILE, stats)
    save_json_file(PENDING_FILE, pending_signals)

    # enviar notificação para o Telegram
    try:
        if result == "GREEN":
            msg = f"🟩 GREEN CONFIRMADO!\n\nSinal: {entry['kind']}\nJogo: {entry['raw']}\n\nTotal:\n🟩 Greens: {stats['greens']}\n🟥 Reds: {stats['reds']}"
        else:
            msg = f"🟥 RED REGISTRADO\n\nSinal: {entry['kind']}\nJogo: {entry['raw']}\n\nTotal:\n🟩 Greens: {stats['greens']}\n🟥 Reds: {stats['reds']}"
        bot.send_message(CHAT_ID, msg)
    except Exception as e:
        logging.warning("Erro enviando confirmação %s: %s", result, e)

# ----------------------------
# Construção de mensagem PREMIUM
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

# ----------------------------
# Análise e envio de sinal
# ----------------------------
def analyze_and_send(match):
    parsed = parse_match_summary_from_raw(match.get("raw",""))
    match_href = match.get("link")
    match_key = match_href or match.get("raw")
    match_info = {"home": parsed.get("home"), "away": parsed.get("away"),
                  "minute": parsed.get("minute"), "score": parsed.get("score"),
                  "league": "Desconhecida"}

    stats_local = fetch_match_stats(match_href) if match_href else None

    analysis = {"corners": None, "attacks": None, "shots": None, "prob": None, "intensity": None, "favor_home": True}
    if stats_local:
        analysis["corners"] = stats_local.get("corners")
        analysis["attacks"] = stats_local.get("attacks")
        analysis["shots"] = stats_local.get("shots")
        intensity = 0
        if analysis["attacks"]:
            intensity += min(100, int(analysis["attacks"] / 2))
        if analysis["shots"]:
            intensity += min(100, int(analysis["shots"] * 3))
        analysis["intensity"] = min(100, intensity)
        analysis["prob"] = min(95, 40 + (analysis["intensity"] // 2))
    else:
        try:
            s1, s2 = map(int, parsed.get("score","0-0").split("-"))
        except:
            s1, s2 = 0, 0
        total = s1 + s2
        analysis["intensity"] = 40 + min(40, total*10)
        analysis["prob"] = 60 + min(30, total*10)

    # Heurísticas simples de sinal
    minute_val = int(parsed.get("minute")) if str(parsed.get("minute")).isdigit() else 0

    # SINAL: GOL — se intensidade alta ou mudança de placar detectada (but avoid duplicates)
    # We'll only proactively suggest gol if intensity high OR shots high OR score low and minute in proper range
    try:
        score_now = parsed.get("score","0-0")
    except:
        score_now = "0-0"

    # Decide send conditions
    send_gol = False
    send_esc = False
    # gol if intensity high and minute between 15-85 and not already sent for this score
    if analysis.get("intensity",0) >= 80 and 15 <= minute_val <= 85:
        send_gol = True

    # escanteio if corners known and rising or above threshold
    if analysis.get("corners") is not None:
        if analysis["corners"] >= 6 and minute_val >= 20:
            send_esc = True

    # avoid duplicate signals for same match & type within short time
    key_g = f"{match_key}-GOL-{score_now}"
    key_e = f"{match_key}-ESC-{analysis.get('corners')}"

    # send gol
    if send_gol and key_g not in sent_signals and key_g not in pending_signals:
        msg = build_premium_message(match_info, analysis, "GOL")
        try:
            bot.send_message(CHAT_ID, msg)
            logging.info("Sinal GOL enviado: %s", match_key)
            sent_signals.add(key_g)
            # store pending: we record sign_score (current score) and sign_corners if available
            add_pending_signal(key_g, "GOL", match_key, score_now, analysis.get("corners"), match.get("raw"))
        except Exception as e:
            logging.error("Erro enviando GOL: %s", e)

    # send esc
    if send_esc and key_e not in sent_signals and key_e not in pending_signals:
        msg = build_premium_message(match_info, analysis, "ESCANTEIO")
        try:
            bot.send_message(CHAT_ID, msg)
            logging.info("Sinal ESC enviado: %s", match_key)
            sent_signals.add(key_e)
            add_pending_signal(key_e, "ESCANTEIO", match_key, score_now, analysis.get("corners"), match.get("raw"))
        except Exception as e:
            logging.error("Erro enviando ESC: %s", e)

# ----------------------------
# Checar pendentes para marcar GREEN / RED
# ----------------------------
def check_pending_with_match(match):
    """Dado um match (raw + link), verificar pendentes relacionados e resolver."""
    parsed = parse_match_summary_from_raw(match.get("raw",""))
    match_key = match.get("link") or match.get("raw")
    current_score = parsed.get("score","0-0")
    status = parsed.get("status","")  # may contain HT/FT

    # fetch stats for corners if needed
    stats_local = fetch_match_stats(match.get("link")) if match.get("link") else None
    current_corners = stats_local.get("corners") if stats_local else None

    to_resolve = []
    for key, entry in list(pending_signals.items()):
        if entry.get("resolved"):
            continue
        if entry.get("match_key") != match_key:
            continue

        # If kind == GOL -> green if score changed vs sign_score
        if entry["kind"] == "GOL":
            if current_score != entry.get("sign_score"):
                # goal happened (score changed)
                resolve_pending_signal(key, "GREEN")
            else:
                # if HT or FT arrived -> mark RED
                if status and status.upper() in ("HT","FT"):
                    resolve_pending_signal(key, "RED")
        elif entry["kind"] == "ESCANTEIO":
            # If we have corners stats and corners increased past sign_corners -> green
            if current_corners is not None and entry.get("sign_corners") is not None:
                if current_corners > (entry.get("sign_corners") or 0):
                    resolve_pending_signal(key, "GREEN")
                else:
                    if status and status.upper() in ("HT","FT"):
                        resolve_pending_signal(key, "RED")
            else:
                # if no corners info available, fallback: if HT/FT -> RED (no confirmation)
                if status and status.upper() in ("HT","FT"):
                    resolve_pending_signal(key, "RED")

# ----------------------------
# Loop principal
# ----------------------------
def run():
    # notify start
    try:
        bot.send_message(CHAT_ID, "🤖 Bot FlashScore (estável) iniciado — monitorando partidas... (GREEN/RED ativo)")
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

            # Primeiro: para todos os candidatos checar pendentes
            for c in candidates:
                try:
                    check_pending_with_match(c)
                except Exception as e:
                    logging.exception("Erro resolvendo pendentes para partida: %s", e)

            # Depois: analisar e possivelmente enviar novos sinais
            for c in candidates:
                try:
                    analyze_and_send(c)
                except Exception as e:
                    logging.exception("Erro analisando partida: %s", e)

            # persist pending and stats periodically
            save_json_file(PENDING_FILE, pending_signals)
            save_json_file(STATS_FILE, stats)

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            logging.info("Interrompido pelo usuário.")
            break
        except Exception as e:
            logging.exception("Erro no loop principal: %s", e)
            time.sleep(POLL_INTERVAL)

# ----------------------------
# Comando simples /stats via Telegram (opcional)
# ----------------------------
@bot.message_handler(commands=['stats'])
def handle_stats(message):
    try:
        g = stats.get("greens",0)
        r = stats.get("reds",0)
        total = g + r
        acc = f"{(g/total*100):.1f}%" if total>0 else "N/A"
        txt = f"📊 Estatísticas do Bot Pedro\n\n🟩 Greens: {g}\n🟥 Reds: {r}\nTaxa de acerto: {acc}"
        bot.reply_to(message, txt)
    except Exception as e:
        logging.error("Erro /stats: %s", e)
        bot.reply_to(message, "Erro ao obter estatísticas.")

if __name__ == "__main__":
    run()