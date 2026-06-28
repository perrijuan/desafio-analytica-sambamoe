# Forecasting Brasil x Japão — Copa 2026

README do projeto de previsão probabilística para **Brasil x Japão**, jogo de mata-mata da Copa 2026. Este material resume o notebook, os modelos usados, a lógica de escolha do ensemble, os gráficos criados para a apresentação e os principais cuidados de auditoria dos dados.

---

## 1. Objetivo

Construir e explicar uma previsão probabilística para o jogo **Brasil x Japão**, usando modelos de forecasting de futebol. O foco não é “adivinhar” o placar, mas estimar probabilidades de:

- vitória do Brasil em 90 minutos;
- empate em 90 minutos;
- vitória do Japão em 90 minutos;
- placares mais prováveis;
- avanço do Brasil no mata-mata.

A apresentação foi feita para pessoas que não são da área técnica, por isso cada modelo foi descrito como uma “lente” diferente sobre o jogo.

---

## 2. Arquivos principais

| Arquivo | Função |
|---|---|
| `copa_2026_hybrid_forecasting_brasil_japao_MOE_MARKOV_CLEAN (1).ipynb` | Notebook original com pipeline, modelos, validação e outputs salvos. |
| `modelos_forecasting_brasil_japao_detalhado_logo.pptx` | Apresentação final detalhada, com a logo enviada e sem gráfico de pizza. |
| `create_detailed_charts.py` | Script usado para gerar os gráficos da versão detalhada. |
| `create_model_explanation_deck_detailed.js` | Script usado para montar a apresentação em PowerPoint. |
| `image.png` | Logo enviada pelo usuário e usada nos slides. |
| `slide_assets_detailed/` | Pasta com gráficos usados na apresentação. |
| `rendered_detailed/` | Renderizações dos slides em imagem para conferência visual. |
| `deck_montage_detailed.png` | Montagem visual com todos os slides. |

---

## 3. Status de auditoria dos dados

O notebook foi analisado a partir do arquivo `.ipynb` enviado. A pasta original `data/gold` **não veio junto no upload**, então nesta sessão os valores foram auditados pelos outputs já salvos dentro do notebook, não por uma reexecução completa end-to-end.

### Dados carregados no notebook

O output do notebook indica que os seguintes arquivos foram lidos com sucesso na execução original:

| Arquivo de entrada | Linhas | Colunas |
|---|---:|---:|
| `worldcup_2026_player_profile_features.csv` | 1.248 | 57 |
| `worldcup_2026_team_profile_features.csv` | 48 | 28 |
| `worldcup_2026_club_distribution.csv` | 823 | 5 |
| `team_matches_2022_2026_free_sources.csv` | 9.256 | 40 |
| `team_matches_2022_2026_unique_fixtures.csv` | 4.628 | 14 |
| `team_goal_scorers_2022_2026_free_sources.csv` | 2.467 | 7 |
| `club_leagues_manual.csv` | 408 | 3 |

Arquivos opcionais ausentes na execução original:

- `goals_team_summary_2022_2026.csv`
- `goals_by_team_context_wide.csv`
- `goals_team_yearly_2022_2026.csv`

---

## 4. Filtro de universo comparável

Antes de treinar os modelos, o notebook restringiu a base a seleções comparáveis dentro do universo da Copa 2026.

| Etapa | Antes | Depois |
|---|---:|---:|
| Linhas de jogos-time | 9.256 | 1.174 |
| Seleções únicas | 262 | 46 |

Média global após o filtro:

```text
1,3356 gol por seleção-jogo
```

Esse valor serve como âncora para os modelos de gols.

---

## 5. Cuidados contra vazamento de dados

O notebook adotou alguns controles importantes:

1. **Features temporais calculadas “as-of”**  
   A performance do ano corrente foi calculada usando apenas jogos anteriores ao jogo de cada linha.

2. **Uso de `shift(1)` nas features rolling**  
   As médias móveis não usam o próprio jogo que estão tentando prever.

3. **Elenco de 2026 fora do treino histórico**  
   O output informa: `Usa squad no treino histórico? False`. Isso reduz risco de usar informação futura para explicar jogos antigos.

4. **Data-alvo respeitada**  
   Para Brasil x Japão, a performance de 2026 foi calculada apenas com jogos anteriores a `2026-06-29`.

---

## 6. Features usadas

O notebook treinou os modelos avançados com **36 features**. Na etapa sem vazamento aparecem **33 features numéricas principais**, incluindo:

- mando/neutralidade;
- tipo de jogo: amistoso ou competitivo;
- mês;
- peso da competição;
- pontos por jogo no ano até antes da partida;
- gols pró por jogo até antes da partida;
- gols contra por jogo até antes da partida;
- saldo por jogo até antes da partida;
- taxa de vitória até antes da partida;
- score de performance normalizado;
- mesmas variáveis para o adversário;
- diferenças entre time e adversário.

---

## 7. Performance 2026 antes da partida

Calculada apenas com jogos antes de **29/06/2026**.

| Time | Jogos 2026 | Pontos/jogo | Gols pró/jogo | Gols contra/jogo | Saldo/jogo | Win rate | Score normalizado |
|---|---:|---:|---:|---:|---:|---:|---:|
| Brasil | 7 | 2,2857 | 2,7143 | 1,0000 | 1,7143 | 0,7143 | 0,7667 |
| Japão | 5 | 2,2000 | 1,8000 | 0,6000 | 1,2000 | 0,6000 | 0,6800 |

Leitura: o Brasil chega com ataque mais forte nos dados recentes; o Japão aparece com defesa recente melhor. Isso ajuda a explicar por que o modelo final não trata o Brasil como favorito absoluto.

---

## 8. Modelos usados

O notebook treinou e combinou cinco especialistas:

```text
['nnar_poisson', 'poisson_double', 'full_logistic', 'elo_logistic', 'prophet_markov']
```

### 8.1 NNAR Poisson

**O que faz:**  
Usa uma rede neural leve para prever gols esperados, respeitando que gols são contagens.

**Como foi usado:**  
Gera `lambda_home` e `lambda_away`, ou seja, os gols esperados de Brasil e Japão.

**Por que entrou:**  
Pode capturar padrões não lineares que um modelo linear simples não percebe.

**Por que não dominou:**  
Na validação temporal de gols, teve erro maior que o Poisson Double.

Output do expert:

| Métrica | Valor |
|---|---:|
| Gols esperados Brasil | 1,0747 |
| Gols esperados Japão | 0,7847 |
| P(Brasil vence 90’) | 42,02% |
| P(empate 90’) | 31,57% |
| P(Japão vence 90’) | 26,41% |

---

### 8.2 Poisson Double Regression

**O que faz:**  
Estima separadamente os gols esperados de cada seleção, usando sinais de ataque, defesa, adversário e contexto.

**Como foi usado:**  
Foi o principal modelo de gols do ensemble.

**Por que foi escolhido:**  
Foi o melhor entre os modelos de gols na validação temporal.

Validação:

| Modelo | MAE | RMSE |
|---|---:|---:|
| NNAR Poisson | 1,2479 | 1,6576 |
| Poisson Double | 1,0194 | 1,3741 |

Output do expert:

| Métrica | Valor |
|---|---:|
| Gols esperados Brasil | 1,4713 |
| Gols esperados Japão | 1,2933 |
| P(Brasil vence 90’) | 41,46% |
| P(empate 90’) | 25,21% |
| P(Japão vence 90’) | 33,33% |

---

### 8.3 Full Logistic

**O que faz:**  
Prevê diretamente vitória, empate ou derrota em 90 minutos, usando várias features do jogo.

**Como foi usado:**  
Serve para calibrar o resultado agregado, especialmente a probabilidade de empate.

**Por que entrou:**  
Modelos só de gols podem perder sinais de cautela, equilíbrio e contexto. O Full Logistic ajuda a corrigir isso.

Output do expert:

| Métrica | Valor |
|---|---:|
| Gols esperados Brasil | 1,2533 |
| Gols esperados Japão | 1,2653 |
| P(Brasil vence 90’) | 26,68% |
| P(empate 90’) | 45,93% |
| P(Japão vence 90’) | 27,39% |

Leitura: esse modelo puxou o empate para cima.

---

### 8.4 Elo Logistic

**O que faz:**  
Usa um rating de força como resumo da qualidade e estabilidade das seleções.

**Como foi usado:**  
Funciona como freio contra exageros de forma recente. Se um time tem poucos jogos recentes muito bons ou ruins, o Elo ajuda a manter a previsão mais estável.

Output do expert:

| Métrica | Valor |
|---|---:|
| Gols esperados Brasil | 1,2583 |
| Gols esperados Japão | 1,3399 |
| P(Brasil vence 90’) | 29,83% |
| P(empate 90’) | 35,46% |
| P(Japão vence 90’) | 34,71% |

---

### 8.5 Prophet + Markov

**O que faz:**  
Combina tendência temporal com uma simulação de placares.

- **Prophet:** lê tendência recente de ataque/defesa quando há histórico suficiente.
- **Markov:** transforma gols esperados em uma matriz de placares possíveis.
- **Ajuste de mata-mata:** tenta refletir que jogos eliminatórios podem ter comportamento diferente de jogos de grupo ou amistosos.

Output do expert:

| Métrica | Valor |
|---|---:|
| Gols esperados Brasil | 1,9050 |
| Gols esperados Japão | 1,5654 |
| P(Brasil vence 90’) | 46,08% |
| P(empate 90’) | 20,22% |
| P(Japão vence 90’) | 33,70% |
| `rho_stage` | 1,0905 |
| `a_star` | 0,6508 |

Limite importante: o Markov usado aqui é simples. Ele não observa cartão, lesão, substituição, mudança tática em tempo real nem reação do time depois de sofrer um gol.

---

## 9. Ensemble MoE: como os pesos foram escolhidos

O modelo final usa um **MoE — Mixture of Experts**, ou Mistura de Especialistas.

Em vez de escolher um único modelo, o notebook testou pares de modelos e atribuiu pesos conforme desempenho na validação. A escolha foi regularizada para evitar que um único par dominasse tudo.

Parâmetros informados nos slides:

| Regra | Valor |
|---|---:|
| Peso máximo por par | 35% |
| Shrinkage uniforme | 25% |
| Penalização de concentração | 0,18 |

### Pesos do ensemble

Fonte: tabela `moe_pair_weights.csv` criada no notebook.  
Coluna usada no slide: `weight_percent = weight * 100`.

| Par de modelos | Peso no ensemble |
|---|---:|
| Poisson Double + Full Logistic | 18,62% |
| Poisson Double + Elo Logistic | 17,49% |
| Elo Logistic + Prophet-Markov | 15,28% |
| Full Logistic + Elo Logistic | 12,50% |
| NNAR Poisson + Poisson Double | 8,48% |
| Poisson Double + Prophet-Markov | 6,62% |
| Full Logistic + Prophet-Markov | 6,06% |
| NNAR Poisson + Elo Logistic | 5,17% |
| NNAR Poisson + Full Logistic | 4,90% |
| NNAR Poisson + Prophet-Markov | 4,86% |

Leitura: o **Poisson Double** aparece nos dois maiores pares, então ele é o eixo mais importante do forecast. Mas o ensemble preserva influência dos modelos logísticos e do Prophet-Markov para não depender de uma única hipótese.

---

## 10. Resultado final do forecast

Fonte: output final do notebook, tabela `moe_brazil_japan_prediction.csv` e resumo impresso na última célula.

| Métrica | Valor |
|---|---:|
| Gols esperados Brasil | 1,40 |
| Gols esperados Japão | 1,28 |
| P(Brasil vence em 90’) | 36,2% |
| P(empate em 90’) | 32,1% |
| P(Japão vence em 90’) | 31,7% |
| P(Brasil avança) | 52,3% |
| IC80 de avanço Brasil | 51,4%–53,2% |

Leitura executiva: o modelo vê **Brasil levemente favorito**, mas não favorito absoluto. O empate em 90 minutos tem peso alto.

---

## 11. Distribuição de placares

Fonte: `score_counts`, salva como `moe_brazil_japan_score_counts_percent.csv`.

A coluna usada nos slides foi:

```python
prob_percent = prob * 100
```

Top placares do output final:

| Placar | Probabilidade |
|---|---:|
| Brasil 1 x 1 Japão | 14,63% |
| Brasil 0 x 0 Japão | 9,56% |
| Brasil 1 x 0 Japão | 8,96% |
| Brasil 0 x 1 Japão | 8,10% |
| Brasil 2 x 1 Japão | 6,89% |
| Brasil 1 x 2 Japão | 6,59% |
| Brasil 2 x 2 Japão | 6,34% |
| Brasil 2 x 0 Japão | 6,13% |
| Brasil 0 x 2 Japão | 5,06% |
| Brasil 3 x 1 Japão | 3,27% |

### Observação sobre o mapa de calor

O mapa de calor dos slides foi montado com os placares mais prováveis do output do notebook em uma grade de 0 a 4 gols. Para máxima rigidez metodológica, a versão final de produção deveria usar diretamente a matriz completa:

```text
moe_brazil_japan_markov_score_matrix.csv
```

ou a variável interna:

```text
score_matrix
```

Assim, o heatmap representaria todos os estados da matriz, não apenas os placares mais relevantes visualmente.

---

## 12. Gráficos criados para a apresentação

A versão detalhada evita gráfico de pizza. Foram usados gráficos mais fáceis de comparar:

| Gráfico | Arquivo | Fonte dos números |
|---|---|---|
| Probabilidades 90 minutos | `chart_wdl_bar.png` | Resultado final do notebook. |
| Gols esperados | `chart_expected_goals_lollipop.png` | `expected_goals_home_final` e `expected_goals_away_final`. |
| Top placares | `chart_top_scores_bar.png` | `score_counts.head(10)`. |
| Mapa de placares | `chart_score_heatmap.png` | Top placares do output, em grade 0–4. |
| Pesos MoE | `chart_moe_weights_bar.png` | `moe_pair_weights.csv`. |
| Validação dos modelos de gols | `chart_validation_grouped_bar.png` | `moe_lambda_expert_validation.csv` / output da célula 20. |
| Performance 2026 | `chart_perf_2026.png` | Performance as-of antes de 2026-06-29. |
| Filtro da base | `chart_data_filter.png` | Relatório de filtro do universo comparável. |
| Discordância dos principais pares | `chart_pair_predictions_stacked.png` | `moe_pair_predictions_brazil_japan.csv`. |

---

## 13. Estrutura da apresentação final

A apresentação detalhada tem 14 slides:

1. Capa — Forecasting Brasil x Japão.
2. Auditoria inicial dos dados.
3. Pipeline em linguagem simples.
4. Modelo 1 — NNAR Poisson.
5. Modelo 2 — Poisson Double Regression.
6. Modelos 3 e 4 — Full Logistic e Elo Logistic.
7. Modelo 5 — Prophet + Markov + ajuste de mata-mata.
8. Como o MoE escolheu os pesos.
9. Como os principais pares discordam.
10. Resultado final em 90 minutos.
11. Distribuição de placares.
12. Por que o modelo não dá “Brasil disparado”.
13. Checklist de qualidade do modelo.
14. Takeaway final.

---

## 14. Como reproduzir os slides

### 14.1 Gerar gráficos

```bash
python create_detailed_charts.py
```

Saída esperada:

```text
/mnt/data/slide_assets_detailed/*.png
```

### 14.2 Gerar PowerPoint

```bash
node create_model_explanation_deck_detailed.js
```

Saída esperada:

```text
/mnt/data/modelos_forecasting_brasil_japao_detalhado_logo.pptx
```

### 14.3 Conferir renderização

As renderizações usadas para conferência visual ficaram em:

```text
/mnt/data/rendered_detailed/
```

E a montagem geral ficou em:

```text
/mnt/data/deck_montage_detailed.png
```

---

## 15. Como reexecutar o notebook original

Para reexecutar o notebook do zero, é necessário ter a estrutura original de dados. O notebook espera arquivos na pasta:

```text
data/gold/
```

Arquivos obrigatórios ou relevantes:

```text
worldcup_2026_player_profile_features.csv
worldcup_2026_team_profile_features.csv
worldcup_2026_club_distribution.csv
team_matches_2022_2026_free_sources.csv
team_matches_2022_2026_unique_fixtures.csv
team_goal_scorers_2022_2026_free_sources.csv
club_leagues_manual.csv
```

O notebook salva os outputs principais em:

```text
data/gold/hybrid_forecasting_outputs/
```

Arquivos finais mais importantes:

```text
moe_brazil_japan_prediction.csv
moe_brazil_japan_score_counts_percent.csv
moe_pair_predictions_brazil_japan.csv
moe_pair_weights.csv
moe_brazil_japan_markov_score_matrix.csv
moe_lambda_expert_validation.csv
```

---

## 16. Limitações conhecidas

1. **Não houve reexecução completa nesta sessão**  
   A pasta `data/gold` não foi enviada, então a auditoria foi feita pelos outputs já salvos no notebook.

2. **Validação ainda é interna**  
   O notebook tem validação temporal com 235 linhas, mas o ideal para padrão acadêmico seria validar em Copas anteriores ou torneios inteiros.

3. **IC80 provavelmente estreito**  
   O intervalo de avanço do Brasil, 51,4%–53,2%, parece estreito para um jogo eliminatório. O ideal seria incorporar incerteza de parâmetros, dependência entre experts, escalações, lesões e mercado.

4. **Markov simples**  
   A matriz de placares não modela eventos em tempo real como cartões, substituições, lesões ou mudança tática depois de um gol.

5. **Prophet/LSTM podem overfit**  
   Séries de seleções são curtas e irregulares. Esse bloco precisa de validação independente para justificar peso alto.

6. **Faltam odds e ratings de mercado**  
   Para ficar mais próximo de papers fortes de forecasting de futebol, seria importante incorporar odds de bookmakers, ratings de jogadores e valor de mercado.

---

## 17. Melhorias recomendadas

Para transformar o protótipo em um modelo mais próximo de padrão de paper:

1. Validar por `match_key`, não por linha, para evitar que uma linha de uma partida caia no treino e a outra na validação.
2. Testar em Copas inteiras: treinar até 2018 e testar 2022; depois treinar até 2022 e testar 2026.
3. Reportar métricas probabilísticas: RPS, Brier Score, log loss 1X2, score de placar e calibração por bins.
4. Comparar contra baselines: Elo-only, Poisson simples, ranking-only e odds-implied probabilities.
5. Usar bootstrap por partidas, parâmetros e experts, não só por pares de experts.
6. Atualizar a previsão perto do jogo com escalações, lesões, suspensões e odds de mercado.
7. Refazer o heatmap final usando a matriz completa `moe_brazil_japan_markov_score_matrix.csv`.

---

## 18. Conclusão

O projeto é um bom protótipo de forecasting híbrido:

- usa múltiplos modelos em vez de depender de uma única hipótese;
- tenta controlar vazamento temporal;
- combina previsão de gols, previsão de resultado e matriz de placares;
- explica o resultado de forma acessível para público não técnico.

A conclusão operacional é:

```text
Brasil levemente favorito, mas jogo equilibrado.
Forecast final auditado no notebook: Brasil avança 52,3%; placar modal 1–1.
```

Esse número deve ser tratado como **estimativa probabilística experimental**, não como certeza. A previsão deve ser atualizada se houver mudanças relevantes em escalação, lesões, suspensões ou odds de mercado.
