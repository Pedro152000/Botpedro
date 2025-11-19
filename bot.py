import requests
import time
import telebot

# ------------------------------------------------
# CONFIGURAÇÕES DO SEU BOT
# ------------------------------------------------
BOT_TOKEN = "8279285665:AAGRi2DQg3Mu3gJmZrKdub_0oHybZKQOSA0"
CHAT_ID = "959511946"
API_KEY = "a649f3ef6e5e4ae597f1bcfd741b6669"

bot = telebot.TeleBot(BOT_TOKEN)

# Endpoint da API grátis
BASE_URL = "https://api.football-data.org/v4/matches"

# Guarda placares enviados para não duplicar avisos
ultimo_placar = {}


def buscar_jogos():
    headers = {"X-Auth-Token": API_KEY}
    try:
        r = requests.get(BASE_URL, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        return data.get("matches", [])
    except Exception as e:
        print("Erro ao buscar jogos:", e)
        return []


def analisar_gols(jogos):
    sinais = []

    for jogo in jogos:
        try:
            status = jogo["status"]
            if status not in ["LIVE", "IN_PLAY", "PAUSED"]:
                continue

            home = jogo["homeTeam"]["name"]
            away = jogo["awayTeam"]["name"]
            placar_casa = jogo["score"]["fullTime"]["home"]
            placar_fora = jogo["score"]["fullTime"]["away"]

            if placar_casa is None:
                placar_casa = 0
            if placar_fora is None:
                placar_fora = 0

            minuto = jogo.get("minute", "AO VIVO")

            partida_id = jogo["id"]
            placar_atual = f"{placar_casa}-{placar_fora}"

            # Evita alertas duplicados
            if ultimo_placar.get(partida_id) == placar_atual:
                continue

            # Se o placar mudou → É GOL
            if partida_id in ultimo_placar and ultimo_placar[partida_id] != placar_atual:
                sinal = (
                    f"⚽ *GOL DETECTADO!*\n\n"
                    f"🏆 Competição: {jogo['competition']['name']}\n"
                    f"📌 Jogo: *{home}* vs *{away}*\n"
                    f"⏱ Minuto: {minuto}\n"
                    f"📊 Placar: *{placar_atual}*\n"
                    f"🔗 Link: https://www.google.com/search?q={home}+vs+{away}"
                )
                sinais.append(sinal)

            ultimo_placar[partida_id] = placar_atual

        except:
            continue

    return sinais


def enviar_sinais():
    jogos = buscar_jogos()
    sinais = analisar_gols(jogos)

    for s in sinais:
        try:
            bot.send_message(CHAT_ID, s, parse_mode="Markdown")
            print("SINAL ENVIADO:", s)
        except Exception as e:
            print("Erro ao enviar:", e)


print("BOT INICIADO — monitorando gols ao vivo...")

while True:
    enviar_sinais()
    time.sleep(60)
