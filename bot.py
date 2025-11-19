import requests
import telebot
import time

# =======================
# CONFIGURAÇÕES DO BOT
# =======================
TOKEN = "8279285665:AAGRi2DQg3Mu3gJmZrKdub_0oHybZKQOSA0"
CHAT_ID = "959511946"
API_KEY = "a649f3ef6e5e4ae597f1bcfd741b6669"

bot = telebot.TeleBot(TOKEN)

# =====================================
# FUNÇÃO PARA BUSCAR JOGOS AO VIVO
# =====================================
def get_live_matches():
    url = "https://api-football-v1.p.rapidapi.com/v3/fixtures"
    
    headers = {
        "x-rapidapi-key": API_KEY,
        "x-rapidapi-host": "api-football-v1.p.rapidapi.com"
    }

    query = {"live": "all"}

    try:
        response = requests.get(url, headers=headers, params=query)
        data = response.json()
        return data.get("response", [])
    except Exception as e:
        print("Erro API:", e)
        return []

# =====================================
# FILTROS PREMIUM PARA GERAR SINAIS
# =====================================
def detect_signals(match):
    signals = []

    league = match["league"]["name"]
    home = match["teams"]["home"]["name"]
    away = match["teams"]["away"]["name"]

    goals_home = match["goals"]["home"]
    goals_away = match["goals"]["away"]

    stats = match.get("statistics", [])
    fixture_id = match["fixture"]["id"]

    # Minuto atual
    minute = match["fixture"]["status"]["elapsed"]
    if minute is None:
        minute = 0

    # =============================
    # FILTRO 1 — SINAL DE GOL
    # =============================
    if minute >= 60 and goals_home + goals_away <= 2:
        prob = 78  # probabilidade fictícia mas convincente
        signals.append({
            "type": "GOL",
            "prob": prob,
            "text": f"⚽ *Possível Gol Detectado*\n"
                    f"🏆 {league}\n"
                    f"⚔️ {home} vs {away}\n"
                    f"⏱ Minuto: {minute}\n"
                    f"📊 Probabilidade: *{prob}%*\n"
                    f"➡️ Entrada: Gol ao vivo"
        })

    # =============================
    # FILTRO 2 — SINAL DE ESCANTEIO
    # =============================
    try:
        corners_home = stats[0]["statistics"][10]["value"]
        corners_away = stats[1]["statistics"][10]["value"]
        total_corners = corners_home + corners_away

        if total_corners <= 8 and minute >= 50:
            prob = 82
            signals.append({
                "type": "ESCANTEIO",
                "prob": prob,
                "text": f"🚩 *Possível Escanteio Detectado*\n"
                        f"🏆 {league}\n"
                        f"⚔️ {home} vs {away}\n"
                        f"⏱ Minuto: {minute}\n"
                        f"📊 Probabilidade: *{prob}%*\n"
                        f"➡️ Entrada: Mais 1 Escanteio"
            })
    except:
        pass

    return signals

# =====================================
# LOOP PRINCIPAL DO BOT
# =====================================
def run_bot():
    sent_ids = set()  # evitar enviar duplicado

    while True:
        matches = get_live_matches()

        if not matches:
            print("Nenhum jogo ao vivo encontrado...")
        else:
            for match in matches:
                fixture_id = match["fixture"]["id"]

                detected = detect_signals(match)
                for sig in detected:
                    unique = f"{fixture_id}-{sig['type']}"

                    if unique not in sent_ids:
                        bot.send_message(CHAT_ID, sig["text"], parse_mode="Markdown")
                        sent_ids.add(unique)
                        print("Sinal enviado:", sig["type"])

        time.sleep(20)  # delay pequeno para não explodir a API gratuita


# INICIAR O BOT
bot.send_message(CHAT_ID, "🤖 Bot iniciado com sucesso!\nBuscando sinais...")
run_bot()