# Freya · Voice assistant for card delivery

The main task in the Freya FDE assignment is to reschedule card deliveries for eight customers who could not be reached on the first attempt. The Turkish-speaking assistant asks whether the customer can talk, verifies their date of birth against the bank service, and offers available courier windows in their district. It records the appointment in SQLite after the customer confirms the selected window in a separate turn.

The call console runs on macOS and simulates outbound calls locally. You can talk to the assistant through your microphone, inspect the trace for each turn, and see how bookings affect courier capacity.

## Run the card delivery assistant

From the project directory, run:

```sh
./run.sh
```

[Open the local console](http://127.0.0.1:8765) and grant microphone access to your browser. The launcher uses the project's virtual environment and local models and starts Ollama if needed. The console runs with a single FastAPI worker. Press Ctrl+C in the terminal to stop it.

### Try your first call

1. Select Ayşe Kaya and click **Görüşmeyi başlat** (Start call). Use the speaker button to replay the opening message.
2. For each reply, click **Mikrofonu başlat** (Start microphone), speak, then click **Kaydı bitir ve gönder** (Stop recording and send). To say you can talk, try “Evet, uygunum” (Yes, I'm available).
3. When asked to verify your identity, say the selected customer's date of birth from the case PDF using the day, month name, and year. The call ends after two failed attempts. For a quick demo, **Doğum tarihini atla** (Skip date of birth) records a bypass in the trace. The demo shows the customer's transcript and the extracted date for the verification turn.
4. Say “Perşembe olmaz, cuma öğleden sonra uygunum” (Thursday doesn't work, but I'm available Friday afternoon). Click the message bubble to see the Whisper transcript, the options sent to Qwen, its JSON output, and the bank's checks. Friday's remaining capacity stays the same while the **Geçici ayrım** (Temporary hold) column shows **Teklifte** (Offered).
5. After the assistant reads out the Friday window, say “Evet” (Yes) in a separate turn. Check the **Randevular** (Appointments) tab for the booking and the change in courier capacity.
6. Try another customer to see how the assistant handles availability. **Veritabanını sıfırla** (Reset database) restores eight customers, five delivery windows, seven available places, and zero appointments. Open calls start over at turn zero. A panel still showing a canceled booking also starts a new call. The assistant explains in text and audio that the previous confirmation is void and the customer should no longer expect a courier.

Use **Görüşme geçmişi** (Call history) to reopen a completed call. Click a turn to see the Whisper transcript, Qwen's interpretation, the decision applied, and the reason if it differed from the model's interpretation. SQLite preserves transcripts and traces across restarts. A database reset clears the call history.

To test temporary holds, open two separate browser tabs. In the first, advance a Kadıköy customer's call until the assistant asks them to confirm Thursday. Then verify a second Kadıköy customer in the other tab. The assistant offers Friday to the second customer. If the first call ends without confirmation, Thursday can be offered again. Each customer can have only one open call.

## Install from source

The card delivery assistant requires Apple Silicon macOS, Python 3.13, `ffmpeg`, `whisper-cli` (whisper.cpp), and Ollama. Obtain the models separately. The app creates its runtime data locally.

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

| Component | Defaults and configuration |
|---|---|
| Whisper | The default multilingual model is `.local/models/ggml-medium.bin`. Set `WHISPER_MODEL` to use a different model. The trace shows which model file was used. [whisper.cpp models](https://huggingface.co/ggerganov/whisper.cpp) |
| Antalia | Place `tr_TR-antalia-medium.onnx` and `tr_TR-antalia-medium.onnx.json` together in `.local/models`. Set `PIPER_MODEL` to use a different ONNX path. [Voice model and attribution](https://huggingface.co/ozgurguler/piper-tr-antalia-medium) |
| Qwen | The Ollama model is `qwen3:4b`. If you use an existing Ollama service, run `ollama pull qwen3:4b` against that service. The project launcher uses `.local/ollama` or the directory selected by `OLLAMA_MODELS` for the service it starts. [Qwen3](https://ollama.com/library/qwen3) |
| Local services | The console listens on `127.0.0.1:8765` and Ollama on `127.0.0.1:11434`. Use `FREYA_PORT` to change the console port and `FREYA_DB` to change the SQLite path. The default database is `.runtime/bank.sqlite`. |

## Architecture and data flow

- `web/` records browser audio with MediaRecorder and plays the assistant's WAV response. It polls events for the active turn by sequence number. Refreshing the tab restores the call while the same server process is running.
- `freya/voice.py` converts audio with ffmpeg and transcribes Turkish speech with Whisper. It first applies rules specific to the current call step. When no rule matches, local Qwen classifies whether the caller agrees to speak, which delivery window they prefer, or whether they confirm the pending offer. Qwen runs with thinking mode disabled. Piper and Antalia voice the response assembled from the bank result and conversation state.
- `freya/engine.py` manages consent, verification, preference, and the separate confirmation turn. It records the reason whenever explicit rejection or uncertainty conflicts with the model's interpretation. A conditional acceptance leads to a revised offer. An unclear reply prompts the assistant to ask again about the same window. Ordinal choices refer to the order in which the assistant read out the options. Qwen interprets natural confirmations in context. The engine keeps the hold and asks again if the transcript lacks evidence of acceptance. Questions and substantive conditions keep the booking pending even when the model classifies the reply as confirmation.
- `freya/bank.py` allows one open call and one confirmed appointment per customer in the demo week. A call temporarily holds each offered delivery window. Once the customer chooses a window, the service releases the others. It books the selected window on explicit confirmation. Rejection, call closure, and reset release the relevant holds. The service checks offers and confirmations inside SQLite write transactions.
- `freya/app.py` serves the browser API. A reset prevents a turn from the previous call from writing to the new one. The API can answer polling requests while model inference or audio generation is in progress. A server restart preserves bookings and traces, closes open calls, and clears temporary holds.
- The demo operator can view synthetic customer records with masked phone numbers. The verification transcript is visible in the demo call history. Qwen attempts to extract the date if the parser cannot. The bank service compares the date against its record exactly. Incoming audio files are temporary. The latest assistant audio response is kept in memory for replay.

## Verification

```sh
.venv/bin/python -m pytest -q
node --test tests/test_panel.mjs
PYTHONPATH=. .venv/bin/python -u tests/smoke_voice.py
```

The first command tests the bank service, call workflow, and HTTP API using temporary SQLite databases. The Node tests run the console's JavaScript with a simulated browser and mocked HTTP responses. The last command runs four voice turns over HTTP using a running local Ollama service, the voice models, and the Turkish macOS Yelda voice. Yelda speaks as the customer. The test converts input to WebM/Opus and fragmented MP4/AAC, generates the assistant's responses with Piper, and uses its own temporary database.

| Initial local run with Whisper small on November 24, 2026 | Observed result |
|---|---|
| Automated regression tests | 21 passed. Coverage included concurrent attempts to book the last place, repeated requests, conditional confirmation, verification limits, model and audio failures, restarts, and resets during generation. The FastAPI test client emitted a deprecation warning. |
| End-to-end call with synthetic speech | Four turns produced one booking for Ayşe Kaya in Kadıköy on Friday from 14:00 to 18:00. A separate regression test verified that a booking is created only after final confirmation. |
| Audio upload to response WAV ready | The turn times were 0.96, 0.83, 2.69, and 0.76 seconds. The median was about 0.90 seconds. Browser playback latency was outside the measurement. |
| Direct preference classification with Qwen | 9 of 12 attempts were correct. Three of four utterances were classified correctly in all three attempts. The model scored 0 of 3 on “Belki cuma gelebilirim, kesin değil” (Maybe I can make Friday, but I'm not sure). A separate test verified that the call engine asks again when the reply is uncertain. |
| Browser check | Verified the local screen, initial tables, opening audio generation and playback events, reset confirmation, and restoration of the initial counts. Human speech through a physical microphone and background noise still need testing. |

### Design changes during testing

- When the model received numeric offer IDs, it marked both days as acceptable. The model output was changed to use day names, with a separate mapping to bank service IDs.
- Short audio recordings were padded with silence. The test used distinct synthetic voices for the assistant and the customer.
- Whisper transcribed the same date as “14 Mart 1988” in WAV and “14 March 1988” in AAC. The date parser now normalizes English month names as well. Verification requires an exact match on day, month, and year.

### Demo limits

The system runs on one computer for a single demo week. Date-of-birth verification uses the case data. A real bank deployment would need reliable verification on outbound calls, authorization, a retention policy, telephony and courier integrations, clear operational ownership, and a pilot with real calls in noisy conditions. The cardholder must show identification at the door. A workflow for requesting extra courier capacity was deferred.

## Additional feature · Freya Piyasa (Nasdaq tracking)

For the assignment's open-ended question, Freya Piyasa simulates a voice call that delivers a market summary when a stock hits a price threshold chosen by the customer.

### Why add this feature?

The Federal Reserve's Survey of Consumer Finances found that **53%** of U.S. families held stocks directly or indirectly in 2019, **58%** in 2022, and **56%** in 2025. Participation dipped in the latest survey and remained **3 percentage points** above 2019. [Federal Reserve, 2019–2022](https://www.federalreserve.gov/publications/october-2023-changes-in-us-family-finances-from-2019-to-2022.htm) · [Federal Reserve, 2025 survey](https://www.federalreserve.gov/newsevents/pressreleases/other20261009a.htm)

Freya Piyasa explores a customer acquisition hypothesis. People interested in investing may be more willing to try a bank that lets them set price alerts for the stocks they follow and hear a short Turkish summary when an alert triggers. The U.S. figures measure stock ownership. This demo did not measure customer acquisition.

In the FINRA Investor Education Foundation's 2024 U.S. investor survey, **29%** of respondents said they used social media groups or forums to inform investment decisions. The share was **60%** among investors aged 18–34. That gives the bank a reason to make it easy to find the price, the reason an alert fired, and the available distribution of analyst ratings in one short summary. [FINRA Foundation research brief, 2026](https://finrafoundation.org/sites/finrafoundation/files/2026-03/FINRA_Foundation_Research_Brief_Social_Media_Finfluencers.pdf)

In the Bank of England and FCA's 2026 survey, **86%** of responding financial firms reported using AI. Among respondents to the short questionnaire for small firms, **36%** identified customer service as a current or planned use of AI. These findings provide context for applying the same voice interface used in card delivery to a second banking scenario where timing matters. [Bank of England and FCA, 2026](https://www.bankofengland.co.uk/report/2026/artificial-intelligence-in-uk-financial-services-2026)

The customer picks a stock, a target price, and a direction. The server monitors the threshold, and the simulated call explains what triggered the alert and summarizes the available market data. The customer can hear about a price move they chose to track without repeatedly checking the quote. The demo did not measure how many customers would prefer a voice alert or how much time it would save.

### Run Freya Piyasa

The market site runs at its own address with a separate database. Piper loads the Turkish Antalia model when it first generates speech. This feature runs independently of the bank console and Ollama.

```sh
./run-nasdaq.sh
```

[Open Freya Piyasa](http://127.0.0.1:8767) and select a stock to inspect its price chart and analyst ratings. Click **Alarm kur** (Set alert) to choose a USD target and direction. Click **Alarmı tetikle** (Trigger alert) to try the workflow immediately. When **Çağrıyı başlat** (Start call) becomes available, start the call and select **Evet, müsaitim** (Yes, I'm available). The assistant reads the alert summary and asks **Başka sorunuz var mı?** (Do you have any other questions?). Select **Hayır** (No) to end the call. All assistant lines in the simulated call use the Turkish Antalia voice. If the browser blocks autoplay, click **Sesi yeniden çal** (Replay audio). The response buttons are available across browsers. Spoken replies depend on browser support.

| Environment variable | Purpose |
|---|---|
| `MASSIVE_API_KEY` | Massive API key for Nasdaq quotes, volume ranking, price history, and automatic alerts. Volume ranking requires Full Market Snapshot access. Without a key, the site uses clearly labeled sample data and supports manual triggers. |
| `FMP_API_KEY` | API key for FMP Grades Summary. Strong Buy and Buy are grouped together, as are Strong Sell and Sell. |
| `NASDAQ_DATA_DELAY_MINUTES` | Market data delay for your plan. Defaults to `15`. Use `0` if your plan includes real-time data. |
| `NASDAQ_POLL_SECONDS` | How often the server checks price targets. Defaults to `60` seconds. |
| `NASDAQ_DB` | Defaults to `.runtime/nasdaq.sqlite`. Alerts are tied to the same browser session and persist across server restarts. |
| `NASDAQ_PORT`, `NASDAQ_BIND` | Default to `8767` and `127.0.0.1`. For external deployments, use an HTTPS proxy and set `NASDAQ_PUBLIC_HOST` to the domain without a port. |

The list ranks Nasdaq-primary-listed common shares and depositary receipts by trading-session share volume. It shows the most recent trading session if the current session has no volume yet. The one-day change uses the previous session's close. The one-week, one-month, and one-year changes use the close on the corresponding calendar date or the closest earlier trading session. Use the mouse or the left and right arrow keys on a desktop to inspect points on the chart.

In live-data mode, the running server checks completed minute bars after an alert is created, even if the browser tab is closed. It checks the chosen direction and triggers the alert once. The simulated call identifies manually triggered alerts as tests. [Massive price data](https://massive.com/docs/rest/stocks/snapshots/full-market-snapshot) · [FMP analyst data](https://site.financialmodelingprep.com/developer/docs/stable/grades-summary)
