/*
 * ============================================================================
 * FarmTech Solutions - Fase 3
 * Firmware de irrigacao com RELOGIO SIMULADO e telemetria em CSV
 * ----------------------------------------------------------------------------
 * Base: firmware da Fase 2 (mesma logica de decisao, mesmos pinos).
 * O que mudou nesta fase:
 *   1. Relogio simulado: cada ciclo de leitura vale 1 HORA de lavoura.
 *      Com um ciclo a cada 250 ms, ~9 minutos de simulacao geram 3 meses.
 *   2. Cada ciclo imprime uma linha "DADOS,..." em formato CSV, pronta para
 *      ser copiada do Monitor Serial e tratada em Python (Pandas/NumPy).
 *   3. O motivo da decisao sai como CODIGO curto (sem virgulas), para nao
 *      quebrar o CSV. A legenda dos codigos esta no README.
 *
 * Cultura alvo: CAFE (Coffea arabica)
 *   - Botoes verdes N/P/K (liga/desliga a cada clique) -> nutrientes
 *   - LDR  -> pH do solo (0 a 14)
 *   - DHT22 -> umidade do solo (%)
 *   - Rele azul -> bomba d'agua
 *
 * Autora: Raiane Sirlene da Silva - RM 575984
 * ============================================================================
 */

#include <DHT.h>

// ---------------------------------------------------------------------------
// MAPEAMENTO DE PINOS (identico a Fase 2)
// ---------------------------------------------------------------------------
#define PINO_DHT        15
#define TIPO_DHT        DHT22
#define PINO_LDR        34
#define PINO_BTN_N      18
#define PINO_BTN_P      19
#define PINO_BTN_K      21
#define PINO_RELE       23

// ---------------------------------------------------------------------------
// PARAMETROS AGRONOMICOS DO CAFE (identicos a Fase 2)
// ---------------------------------------------------------------------------
const float PH_MINIMO          = 5.0;
const float PH_MAXIMO          = 6.8;
const float UMIDADE_CRITICA    = 35.0;
const float UMIDADE_PREVENTIVA = 50.0;
const float UMIDADE_MAXIMA     = 60.0;

const int   ADC_MAXIMO         = 4095;
const float PH_ESCALA_MAXIMA   = 14.0;

// Um ciclo a cada 250 ms = 1 hora simulada.
// (O DHT22 so atualiza a cada 2 s; entre atualizacoes a biblioteca devolve
//  a ultima leitura, o que nao atrapalha o historico.)
const unsigned long INTERVALO_CICLO = 250;  // ms

// ---------------------------------------------------------------------------
// RELOGIO SIMULADO - inicio do historico: 01/07/2026 00:00
// ---------------------------------------------------------------------------
int anoSim  = 2026;
int mesSim  = 7;
int diaSim  = 1;
int horaSim = 0;

int diasNoMes(int mes, int ano) {
  const int dias[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
  bool bissexto = (ano % 4 == 0 && ano % 100 != 0) || (ano % 400 == 0);
  if (mes == 2 && bissexto) return 29;
  return dias[mes - 1];
}

// Avanca o relogio em 1 hora, virando dia/mes/ano quando necessario
void avancarUmaHora() {
  horaSim++;
  if (horaSim < 24) return;
  horaSim = 0;
  diaSim++;
  if (diaSim <= diasNoMes(mesSim, anoSim)) return;
  diaSim = 1;
  mesSim++;
  if (mesSim <= 12) return;
  mesSim = 1;
  anoSim++;
}

// ---------------------------------------------------------------------------
// ESTADO GLOBAL
// ---------------------------------------------------------------------------
DHT dht(PINO_DHT, TIPO_DHT);

bool bombaLigada = false;
unsigned long ultimoCiclo = 0;
unsigned long ciclo = 0;

// Nutrientes retidos (botao do Wokwi e momentaneo: cada clique alterna)
bool nivelN = false, nivelP = false, nivelK = false;
bool ultimoBtnN = HIGH, ultimoBtnP = HIGH, ultimoBtnK = HIGH;

// ---------------------------------------------------------------------------
// LEITURA DOS SENSORES
// ---------------------------------------------------------------------------
// Detecta a borda de descida (clique) e alterna o nivel do nutriente
void atualizarBotao(int pino, bool &ultimo, bool &nivel) {
  bool atual = digitalRead(pino);
  if (ultimo == HIGH && atual == LOW) {
    nivel = !nivel;
  }
  ultimo = atual;
}

int lerLdrBruto() {
  return analogRead(PINO_LDR);
}

float converterPh(int bruto) {
  float ph = (bruto * PH_ESCALA_MAXIMA) / ADC_MAXIMO;
  if (ph < 0.0) ph = 0.0;
  if (ph > PH_ESCALA_MAXIMA) ph = PH_ESCALA_MAXIMA;
  return ph;
}

float lerUmidade() {
  float umidade = dht.readHumidity();
  if (isnan(umidade)) return -1.0;   // falha de leitura
  return umidade;
}

// ---------------------------------------------------------------------------
// LOGICA DE DECISAO (mesma regra da Fase 2)
// Codigos de motivo:
//   FALHA_SENSOR | SATURADO | PH_FORA | EMERGENCIA | FERTIRRIGACAO |
//   NPK_INCOMPLETO | HISTERESE_LIGADA | UMIDADE_OK
// ---------------------------------------------------------------------------
bool decidirIrrigacao(float umidade, float ph, bool n, bool p, bool k,
                      const char *&motivo) {
  if (umidade < 0.0)              { motivo = "FALHA_SENSOR";  return false; }
  if (umidade >= UMIDADE_MAXIMA)  { motivo = "SATURADO";      return false; }
  if (ph < PH_MINIMO || ph > PH_MAXIMO) { motivo = "PH_FORA"; return false; }
  if (umidade < UMIDADE_CRITICA)  { motivo = "EMERGENCIA";    return true;  }
  if (umidade < UMIDADE_PREVENTIVA) {
    if (n && p && k) { motivo = "FERTIRRIGACAO";  return true;  }
    motivo = "NPK_INCOMPLETO";
    return false;
  }
  // 50% a 60%: histerese, mantem o estado anterior
  motivo = bombaLigada ? "HISTERESE_LIGADA" : "UMIDADE_OK";
  return bombaLigada;
}

// ---------------------------------------------------------------------------
// TELEMETRIA CSV
// ---------------------------------------------------------------------------
void imprimirCabecalho() {
  Serial.println();
  Serial.println("# FARMTECH SOLUTIONS - FASE 3 - TELEMETRIA CSV (1 ciclo = 1 hora)");
  Serial.println("# Copie as linhas abaixo para data/raw/monitor_serial_wokwi.txt");
  Serial.println("tipo,data_hora,ciclo,umidade,ph,ldr_bruto,nitrogenio,fosforo,potassio,bomba,motivo");
}

void imprimirLinha(float umidade, float ph, int ldr, const char *motivo) {
  char dataHora[20];
  snprintf(dataHora, sizeof(dataHora), "%04d-%02d-%02d %02d:00",
           anoSim, mesSim, diaSim, horaSim);

  char textoUmidade[10];
  if (umidade < 0.0) snprintf(textoUmidade, sizeof(textoUmidade), "ERRO");
  else               snprintf(textoUmidade, sizeof(textoUmidade), "%.1f", umidade);

  char linha[140];
  snprintf(linha, sizeof(linha), "DADOS,%s,%lu,%s,%.2f,%d,%d,%d,%d,%s,%s",
           dataHora, ciclo, textoUmidade, ph, ldr,
           nivelN ? 1 : 0, nivelP ? 1 : 0, nivelK ? 1 : 0,
           bombaLigada ? "LIGA" : "DESL", motivo);
  Serial.println(linha);
}

// ---------------------------------------------------------------------------
// SETUP
// ---------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(500);

  pinMode(PINO_BTN_N, INPUT_PULLUP);
  pinMode(PINO_BTN_P, INPUT_PULLUP);
  pinMode(PINO_BTN_K, INPUT_PULLUP);
  pinMode(PINO_RELE, OUTPUT);
  digitalWrite(PINO_RELE, LOW);

  analogReadResolution(12);
  dht.begin();

  imprimirCabecalho();
}

// ---------------------------------------------------------------------------
// LOOP PRINCIPAL
// ---------------------------------------------------------------------------
void loop() {
  // Os botoes sao lidos o tempo todo, para nao perder cliques entre ciclos
  atualizarBotao(PINO_BTN_N, ultimoBtnN, nivelN);
  atualizarBotao(PINO_BTN_P, ultimoBtnP, nivelP);
  atualizarBotao(PINO_BTN_K, ultimoBtnK, nivelK);

  unsigned long agora = millis();
  if (agora - ultimoCiclo < INTERVALO_CICLO) return;
  ultimoCiclo = agora;
  ciclo++;

  int   ldr     = lerLdrBruto();
  float ph      = converterPh(ldr);
  float umidade = lerUmidade();

  const char *motivo;
  bool novoEstado = decidirIrrigacao(umidade, ph, nivelN, nivelP, nivelK, motivo);

  if (novoEstado != bombaLigada) {
    bombaLigada = novoEstado;
    digitalWrite(PINO_RELE, bombaLigada ? HIGH : LOW);
  }

  imprimirLinha(umidade, ph, ldr, motivo);
  avancarUmaHora();
}
