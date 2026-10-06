# ORQETIA Client API v1 — Manual técnico de integração

**Status do produto:** DEVELOPMENT  
**Contrato canônico:** contracts/openapi/orqetia-v1.openapi.json  
**Base path:** /v1  
**Issue:** #27

Este manual descreve o comportamento efetivamente entregue da Client API v1 e do
runtime de sessions/tasks. O OpenAPI continua sendo a fonte canônica de schemas.
Quando houver divergência, o contrato e o runtime devem ser reconciliados no mesmo
change set; este manual não cria um contrato paralelo.

## 1. Princípios de segurança e ownership

Toda chamada autenticada opera no tenant/client do principal Bearer. O cliente não
declara tenant_id ou client_id para ganhar autoridade. Recursos de outro
tenant/client são negados ou tratados como não encontrados de forma segura.

A Client API nunca retorna:

- provider secret ou chave do provider;
- provider account ou provider credential;
- créditos/saldo do provider;
- provider cost, preço contratado, unit price ou currency interna;
- client_charge, markup ou condição comercial interna.

Tentativas e exchange evidence usam DTOs client-safe. Evidence bruto é sempre a
versão sanitizada persistida; presentation layers não devem reinterpretar ou
"humanizar" semanticamente sanitized_raw_body.

## 2. Autenticação S2S

Envie um Bearer aceito pelo deployment:

~~~bash
export ORQETIA_BASE_URL="https://orqetia.example"
export ORQETIA_TOKEN="<bearer-secret>"

curl -sS "$ORQETIA_BASE_URL/v1/providers"   -H "Authorization: Bearer $ORQETIA_TOKEN"
~~~

Credenciais de integração emitidas por POST /v1/credentials têm secret one-time.
O secret deve ser armazenado pelo cliente no momento da emissão/rotação. A API
armazena material verificável, não uma cópia recuperável do secret.

A ORQETIA não expõe um token endpoint OAuth próprio neste contrato. O deployment
pode aceitar o bearer de integração ORQETIA ou um bearer produzido pela camada de
AuthN configurada, desde que o principal resultante possua owner e scopes válidos.

## 3. Headers e convenções globais

| Header | Regra |
| --- | --- |
| Authorization | Bearer obrigatório em todas as operações /v1. |
| Content-Type | application/json nos endpoints com body JSON. |
| Idempotency-Key | Obrigatório nas mutações indicadas; 1..200 caracteres. |
| X-Correlation-ID | Opcional; [A-Za-z0-9._:-], 1..200. Valor inválido é substituído por UUID server-side. |
| Retry-After | Pode aparecer quando houver limitação/retry aplicável; respeite quando presente. |

Toda resposta inclui X-Correlation-ID. Reenvie o mesmo valor em logs do cliente
para facilitar suporte e correlação.

## 4. Idempotência

Session create, task create, task cancel e lifecycle de credentials usam
Idempotency-Key. A identidade da chave é scoped por owner/operação. Repetir a
mesma chave com a mesma requisição resolve para o mesmo efeito/recurso quando a
operação possui journal durável. Reusar a mesma chave para uma requisição
semanticamente diferente resulta em conflito 409.

Nunca use tenant_id, client_id, task_id ou session_id como substituto do
Idempotency-Key.

## 5. Error envelope

Erros HTTP são sanitizados no formato:

~~~json
{
  "code": "INVALID_REQUEST",
  "message": "Request validation failed.",
  "correlation_id": "8b6f7e60-37e9-4cef-9f31-9848853a65a5",
  "details": [
    {"field": "input", "reason": "Invalid value"}
  ]
}
~~~

Códigos HTTP recorrentes:

| HTTP | Significado |
| ---: | --- |
| 400/422 | Request/schema/semântica inválida ou estimate seguro indisponível. |
| 401 | Bearer ausente, inválido ou rejeitado. |
| 403 | Scope/target/permissão insuficiente. |
| 404 | Recurso ownership-scoped não encontrado/visível. |
| 409 | Idempotency/state conflict ou resultado ainda não disponível. |
| 413 | Conteúdo acima do limite server-configured. |
| 429 | Quota/rate/backpressure aplicável. |
| 503 | Adapter/service necessário não configurado ou indisponível. |

## 6. AUTO e EXPLICIT_TARGET

AUTO é o default quando execution é omitido. O client não envia requirements,
retry policy, max cycles, delays, timeouts, quarantine ou quota configuration.

Em AUTO:

1. somente targets elegíveis e autorizados entram no pool;
2. targets com custo comparável são ordenados pelo menor provider-cost interno;
3. custo sobe somente quando o target anterior não satisfaz o requisito restante;
4. accepted/missing parcial é preservado;
5. o pool/order pode ser reavaliado por cycle;
6. UNPRICED permanece elegível e nunca significa custo zero;
7. não existe FX implícito entre moedas/grupos não comparáveis.

Em EXPLICIT_TARGET, provider/model/reasoning_profile ficam fixos. Retry/cycles
continuam sob a policy da ORQETIA, mas não ocorre fallback cross-target por custo.
Além de tasks:write, o principal precisa de tasks:target.

### Quickstart AUTO

Crie a session:

~~~bash
curl -sS -X POST "$ORQETIA_BASE_URL/v1/sessions"   -H "Authorization: Bearer $ORQETIA_TOKEN"   -H "Content-Type: application/json"   -H "Idempotency-Key: demo-session-001"   -d '{"external_reference":"demo-auto"}'
~~~

Use o session_id retornado:

~~~bash
export SESSION_ID="<session-uuid>"
~~~

<!-- QUICKSTART_AUTO_JSON_START -->
~~~json
{
  "operation": "TASK_EXECUTION",
  "input": {
    "input_text": "Return a short JSON summary."
  },
  "external_reference": "demo-auto-task"
}
~~~
<!-- QUICKSTART_AUTO_JSON_END -->

~~~bash
curl -sS -X POST   "$ORQETIA_BASE_URL/v1/sessions/$SESSION_ID/tasks"   -H "Authorization: Bearer $ORQETIA_TOKEN"   -H "Content-Type: application/json"   -H "Idempotency-Key: demo-task-auto-001"   -d '{
    "operation":"TASK_EXECUTION",
    "input":{"input_text":"Return a short JSON summary."},
    "external_reference":"demo-auto-task"
  }'
~~~

A ausência de execution ativa AUTO.

### Quickstart EXPLICIT_TARGET

Primeiro consulte /v1/providers e /v1/models e escolha somente uma combinação
visível/autorizada.

<!-- QUICKSTART_EXPLICIT_JSON_START -->
~~~json
{
  "operation": "TASK_EXECUTION",
  "input": {
    "input_text": "Return a short JSON summary."
  },
  "execution": {
    "mode": "EXPLICIT_TARGET",
    "target": {
      "provider": "provider-visible-id",
      "model": "model-visible-id",
      "reasoning_profile": "standard"
    }
  },
  "external_reference": "demo-explicit-task"
}
~~~
<!-- QUICKSTART_EXPLICIT_JSON_END -->

~~~bash
curl -sS -X POST   "$ORQETIA_BASE_URL/v1/sessions/$SESSION_ID/tasks"   -H "Authorization: Bearer $ORQETIA_TOKEN"   -H "Content-Type: application/json"   -H "Idempotency-Key: demo-task-explicit-001"   -d '{
    "operation":"TASK_EXECUTION",
    "input":{"input_text":"Return a short JSON summary."},
    "execution":{
      "mode":"EXPLICIT_TARGET",
      "target":{
        "provider":"provider-visible-id",
        "model":"model-visible-id",
        "reasoning_profile":"standard"
      }
    },
    "external_reference":"demo-explicit-task"
  }'
~~~

Se o target não estiver no envelope da session, a API rejeita server-side. Não
há fallback para outro provider/model/profile por custo.

## 7. Lifecycle de task e polling

Estados client-facing: CREATED, QUEUED, RUNNING, PARTIAL, COMPLETE, UNAVAILABLE,
FAILED, CANCELLING e CANCELLED.

Fluxo normal: criar task (202), fazer polling de GET /v1/tasks/{task_id}, e quando
apropriado consultar /result. PARTIAL preserva accepted/missing. Attempts e
exchange evidence são superfícies de diagnóstico técnico client-safe.

Exemplo de polling simples:

~~~bash
export TASK_ID="<task-uuid>"

curl -sS "$ORQETIA_BASE_URL/v1/tasks/$TASK_ID"   -H "Authorization: Bearer $ORQETIA_TOKEN"

curl -sS "$ORQETIA_BASE_URL/v1/tasks/$TASK_ID/result"   -H "Authorization: Bearer $ORQETIA_TOKEN"
~~~

## 8. Quotas, limits e backpressure

Quotas são administrativas; o client não define nem altera limites no body.
Policies aplicáveis podem limitar tasks, concorrência, provider requests, tokens
ou native units. O enforcement ocorre server-side. Um 429 deve ser tratado como
limitação temporária/administrativa; respeite Retry-After quando enviado.

POST de task/estimate está sujeito a content limit server-configured e pode
retornar 413. Paginação usa limit 1..100, default 50.

Quotas internas de client não expõem provider credits nem provider balances.

## 9. Endpoints v1

Cada seção abaixo usa os schemas do OpenAPI canônico. "Erros" lista os estados
declarados no contrato; 5xx sanitizados também podem ocorrer para falha interna
ou adapter indisponível. Todos os endpoints são v1 e não estão marcados como
deprecated no contrato atual.

### POST /v1/sessions

**Finalidade:** cria uma execution session e congela a effective execution policy
do owner autenticado.

| Item | Contrato |
| --- | --- |
| Scope | sessions:write |
| Ownership | tenant/client autenticado |
| Headers | Authorization, Content-Type, Idempotency-Key; X-Correlation-ID opcional |
| Request | SessionCreateRequest; external_reference opcional, até 200 |
| Success | 201 SessionView |
| Erros declarados | 401, 403, 404, 409, 413, 422, 429 |
| Idempotência | Sim; mesma key/request resolve para a mesma session |
| Limites/quota | Policy server-side; client não define cycles/retries/quotas |
| Version/deprecation | v1; não deprecated |

cURL:

~~~bash
curl -sS -X POST "$ORQETIA_BASE_URL/v1/sessions"   -H "Authorization: Bearer $ORQETIA_TOKEN"   -H "Content-Type: application/json"   -H "Idempotency-Key: session-001"   -d '{"external_reference":"job-42"}'
~~~

### GET /v1/sessions/{session_id}

**Finalidade:** consulta uma session própria.

| Item | Contrato |
| --- | --- |
| Scope | sessions:read |
| Ownership | session deve pertencer ao owner autenticado |
| Params | session_id UUID no path |
| Success | 200 SessionView |
| Erros declarados | 401, 403, 404, 429 |
| Idempotência | Não aplicável (GET) |
| Limites/quota | Read quota/backpressure server-side |
| Version/deprecation | v1; não deprecated |

cURL:

~~~bash
curl -sS "$ORQETIA_BASE_URL/v1/sessions/$SESSION_ID"   -H "Authorization: Bearer $ORQETIA_TOKEN"
~~~

### POST /v1/sessions/{session_id}/tasks

**Finalidade:** cria trabalho assíncrono durável em uma session própria.

| Item | Contrato |
| --- | --- |
| Scope | tasks:write; tasks:target adicional para EXPLICIT_TARGET |
| Ownership | session deve pertencer ao owner autenticado |
| Headers | Authorization, Content-Type, Idempotency-Key |
| Request | TaskCreateRequest: operation, input, execution opcional, external_reference opcional |
| Success | 202 TaskView |
| Erros declarados | 401, 403, 404, 409, 413, 422, 429 |
| Idempotência | Sim; submission/queue é durável |
| Limites/quota | Content limit e quotas server-side |
| Version/deprecation | v1; não deprecated |

cURL: AUTO

~~~bash
curl -sS -X POST   "$ORQETIA_BASE_URL/v1/sessions/$SESSION_ID/tasks"   -H "Authorization: Bearer $ORQETIA_TOKEN"   -H "Content-Type: application/json"   -H "Idempotency-Key: task-001"   -d '{"operation":"TASK_EXECUTION","input":{"input_text":"hello"}}'
~~~

### GET /v1/tasks/{task_id}

**Finalidade:** consulta estado e provenance client-safe de uma task própria.

| Item | Contrato |
| --- | --- |
| Scope | tasks:read |
| Ownership | task deve pertencer ao owner autenticado |
| Params | task_id UUID |
| Success | 200 TaskView |
| Erros declarados | 401, 403, 404, 429 |
| Idempotência | Não aplicável |
| Limites/quota | Read quota/backpressure server-side |
| Version/deprecation | v1; não deprecated |

cURL:

~~~bash
curl -sS "$ORQETIA_BASE_URL/v1/tasks/$TASK_ID"   -H "Authorization: Bearer $ORQETIA_TOKEN"
~~~

### GET /v1/tasks/{task_id}/result

**Finalidade:** retorna o resultado client-safe atual/terminal com accepted/missing
e attempt_ids.

| Item | Contrato |
| --- | --- |
| Scope | tasks:read |
| Ownership | task deve pertencer ao owner autenticado |
| Params | task_id UUID |
| Success | 200 TaskResultView |
| Erros declarados | 401, 403, 404, 409, 429 |
| Idempotência | Não aplicável |
| Limites/quota | Artifact retention pode tornar resultado indisponível |
| Version/deprecation | v1; não deprecated |

cURL:

~~~bash
curl -sS "$ORQETIA_BASE_URL/v1/tasks/$TASK_ID/result"   -H "Authorization: Bearer $ORQETIA_TOKEN"
~~~

### POST /v1/tasks/{task_id}/cancel

**Finalidade:** solicita cancellation idempotente da task.

| Item | Contrato |
| --- | --- |
| Scope | tasks:cancel |
| Ownership | task deve pertencer ao owner autenticado |
| Headers | Authorization, Idempotency-Key |
| Success | 202 TaskView |
| Erros declarados | 401, 403, 404, 409, 429 |
| Idempotência | Sim |
| Limites/quota | Estado terminal não é revertido |
| Version/deprecation | v1; não deprecated |

cURL:

~~~bash
curl -sS -X POST "$ORQETIA_BASE_URL/v1/tasks/$TASK_ID/cancel"   -H "Authorization: Bearer $ORQETIA_TOKEN"   -H "Idempotency-Key: cancel-001"
~~~

### GET /v1/tasks/{task_id}/attempts

**Finalidade:** lista provenance de attempts sem dados financeiros/provider-secret.

| Item | Contrato |
| --- | --- |
| Scope | tasks:read |
| Ownership | task/attempts do owner autenticado |
| Query | cursor opcional; limit 1..100, default 50 |
| Success | 200 object com items AttemptView e next_cursor |
| Erros declarados | 401, 403, 404, 429 |
| Idempotência | Não aplicável |
| Limites/quota | Paginação determinística |
| Version/deprecation | v1; não deprecated |

AttemptView pode conter provider identity client-safe, model, reasoning, status,
cycle, attempt_index, retry/fallback provenance, technical usage e timestamps.
Não contém provider account, provider credential, provider cost ou currency.

cURL:

~~~bash
curl -sS   "$ORQETIA_BASE_URL/v1/tasks/$TASK_ID/attempts?limit=50"   -H "Authorization: Bearer $ORQETIA_TOKEN"
~~~

### GET /v1/tasks/{task_id}/attempts/{attempt_id}/exchanges

**Finalidade:** retorna evidence sanitizada retida para um attempt próprio.

| Item | Contrato |
| --- | --- |
| Scope | tasks:read |
| Ownership | task e attempt do owner autenticado |
| Params | task_id UUID; attempt_id UUID |
| Success | 200 object com items ExchangeEvidenceView |
| Erros declarados | 401, 403, 404, 429 |
| Idempotência | Não aplicável |
| Limites/quota | Somente evidence retida; body pode estar truncated |
| Version/deprecation | v1; não deprecated |

SanitizedEvidence contém media_type, sanitized_raw_body, sanitized_sha256 e
truncated. status_label é metadata humanizável; sanitized_raw_body não é.

cURL:

~~~bash
export ATTEMPT_ID="<attempt-uuid>"

curl -sS   "$ORQETIA_BASE_URL/v1/tasks/$TASK_ID/attempts/$ATTEMPT_ID/exchanges"   -H "Authorization: Bearer $ORQETIA_TOKEN"
~~~

### POST /v1/estimates

**Finalidade:** estima usage técnico sem chamada ao provider e sem output
monetário.

| Item | Contrato |
| --- | --- |
| Scope | estimates:write; tasks:target adicional para EXPLICIT_TARGET |
| Ownership | tenant/client autenticado |
| Headers | Authorization, Content-Type, Idempotency-Key |
| Request | EstimateRequest |
| Success | 200 EstimateResponse |
| Erros declarados | 401, 403, 404, 409, 413, 422, 429 |
| Idempotência | Header obrigatório pelo contrato; operação é provider-free |
| Limites/quota | Content limit server-side; benchmark governado |
| Version/deprecation | v1; não deprecated |

reference_scope é CLIENT_ONLY por default ou GLOBAL_PUBLIC. Resposta pode incluir
input/cached/output/reasoning/total tokens, effective target, methodology,
benchmark version, as_of, sample/cohort size, confidence e limitações. Nunca
retorna provider cost/currency/pricing/client_charge.

cURL:

~~~bash
curl -sS -X POST "$ORQETIA_BASE_URL/v1/estimates"   -H "Authorization: Bearer $ORQETIA_TOKEN"   -H "Content-Type: application/json"   -H "Idempotency-Key: estimate-001"   -d '{
    "operation":"TASK_EXECUTION",
    "input":{"input_text":"hello"},
    "reference_scope":"CLIENT_ONLY"
  }'
~~~

### GET /v1/providers

**Finalidade:** lista providers visíveis no envelope autorizado do client.

| Item | Contrato |
| --- | --- |
| Scope | catalog:read |
| Ownership | resultado filtrado ao envelope do owner autenticado |
| Success | 200 array ProviderPublicView |
| Erros declarados | 401, 403, 404, 429 |
| Idempotência | Não aplicável |
| Limites/quota | Nenhum dado de account/pricing/credits |
| Version/deprecation | v1; não deprecated |

cURL:

~~~bash
curl -sS "$ORQETIA_BASE_URL/v1/providers"   -H "Authorization: Bearer $ORQETIA_TOKEN"
~~~

### GET /v1/models

**Finalidade:** lista models/reasoning profiles visíveis no envelope do client.

| Item | Contrato |
| --- | --- |
| Scope | catalog:read |
| Ownership | resultado filtrado ao envelope do owner autenticado |
| Query | provider_id opcional, até 100 caracteres |
| Success | 200 array ModelPublicView |
| Erros declarados | 401, 403, 404, 429 |
| Idempotência | Não aplicável |
| Limites/quota | Apenas capabilities/reasoning client-safe |
| Version/deprecation | v1; não deprecated |

cURL:

~~~bash
curl -sS "$ORQETIA_BASE_URL/v1/models?provider_id=provider-visible-id"   -H "Authorization: Bearer $ORQETIA_TOKEN"
~~~

### GET /v1/usage

**Finalidade:** retorna rollups técnicos do owner autenticado, sem financeiro.

| Item | Contrato |
| --- | --- |
| Scope | usage:read |
| Ownership | usage filtrado ao tenant/client autenticado |
| Query | cursor; limit 1..100 default 50; from/to date-time opcionais |
| Success | 200 UsagePage |
| Erros declarados | 400, 401, 403, 404, 413, 429 |
| Idempotência | Não aplicável |
| Limites/quota | Read model paginado; scan de source rows é bounded e excesso retorna 413 |
| Version/deprecation | v1; não deprecated |

Usage inclui tokens técnicos e native usage não monetária. Não inclui amount,
currency, pricing, provider account ou provider credential.

cURL:

~~~bash
curl -sS   "$ORQETIA_BASE_URL/v1/usage?limit=50&from=2026-10-01T00:00:00Z&to=2026-10-31T23:59:59Z"   -H "Authorization: Bearer $ORQETIA_TOKEN"
~~~

### GET /v1/credentials

**Finalidade:** lista metadata das credenciais de integração do próprio owner.

| Item | Contrato |
| --- | --- |
| Scope | credentials:read |
| Ownership | credentials do tenant/client autenticado |
| Success | 200 CredentialListResponse |
| Erros declarados | 401, 403, 404, 409, 429 |
| Idempotência | Não aplicável |
| Limites/quota | Secret/hash/salt nunca são listados |
| Version/deprecation | v1; não deprecated |

cURL:

~~~bash
curl -sS "$ORQETIA_BASE_URL/v1/credentials"   -H "Authorization: Bearer $ORQETIA_TOKEN"
~~~

### POST /v1/credentials

**Finalidade:** emite credential de integração; o caller não pode delegar scope
que ele próprio não possua.

| Item | Contrato |
| --- | --- |
| Scope | credentials:write |
| Ownership | credential vinculada ao tenant/client autenticado |
| Headers | Authorization, Content-Type, Idempotency-Key |
| Request | CredentialCreateRequest: display_label, scopes, expires_at opcional |
| Success | 200 CredentialIssueResponse |
| Erros declarados | 401, 403, 404, 409, 429 |
| Idempotência | Sim; replay não reexibe secret |
| Limites/quota | scopes 1..50; label 1..200 |
| Version/deprecation | v1; não deprecated |

secret é one-time. Em idempotent replay, secret é null, secret_available=false e
replayed=true.

cURL:

~~~bash
curl -sS -X POST "$ORQETIA_BASE_URL/v1/credentials"   -H "Authorization: Bearer $ORQETIA_TOKEN"   -H "Content-Type: application/json"   -H "Idempotency-Key: credential-issue-001"   -d '{
    "display_label":"production-worker",
    "scopes":["sessions:read","sessions:write","tasks:read","tasks:write"]
  }'
~~~

### POST /v1/credentials/{credential_id}/rotate

**Finalidade:** rotaciona credential própria e invalida o secret anterior.

| Item | Contrato |
| --- | --- |
| Scope | credentials:write |
| Ownership | credential do tenant/client autenticado |
| Params | credential_id UUID |
| Headers | Authorization, Idempotency-Key |
| Success | 200 CredentialIssueResponse |
| Erros declarados | 401, 403, 404, 409, 429 |
| Idempotência | Sim; secret novo é one-time e não reaparece no replay |
| Limites/quota | Estado inválido produz 409 |
| Version/deprecation | v1; não deprecated |

cURL:

~~~bash
export CREDENTIAL_ID="<credential-uuid>"

curl -sS -X POST   "$ORQETIA_BASE_URL/v1/credentials/$CREDENTIAL_ID/rotate"   -H "Authorization: Bearer $ORQETIA_TOKEN"   -H "Idempotency-Key: credential-rotate-001"
~~~

### POST /v1/credentials/{credential_id}/revoke

**Finalidade:** revoga credential própria.

| Item | Contrato |
| --- | --- |
| Scope | credentials:write |
| Ownership | credential do tenant/client autenticado |
| Params | credential_id UUID |
| Headers | Authorization, Idempotency-Key |
| Success | 200 CredentialMetadataView |
| Erros declarados | 401, 403, 404, 409, 429 |
| Idempotência | Sim |
| Limites/quota | Secret revogado deixa de autenticar |
| Version/deprecation | v1; não deprecated |

cURL:

~~~bash
curl -sS -X POST   "$ORQETIA_BASE_URL/v1/credentials/$CREDENTIAL_ID/revoke"   -H "Authorization: Bearer $ORQETIA_TOKEN"   -H "Idempotency-Key: credential-revoke-001"
~~~

## 10. Correlation, observability e retries do cliente

- Preserve X-Correlation-ID de response nos logs.
- Não faça retry cego de 4xx.
- Para mutation retry por timeout/rede, reutilize o mesmo Idempotency-Key e a
  mesma requisição.
- Para 429/503 transitório, use backoff e Retry-After quando presente.
- Não reutilize Idempotency-Key para payload semanticamente diferente.

Provider retries/cycles/fallback pertencem ao orchestration layer da ORQETIA; o
client não implementa nem controla esse loop.

## 11. Retention e sanitized evidence

Result/evidence são lidos por referências duráveis client-private. Retention pode
remover conteúdo conforme policy operacional futura; um artifact ausente não
autoriza acesso ao storage interno.

Exchange evidence contém somente conteúdo sanitizado. Não use a evidence API como
substituto de provider logs privados ou provider credentials.

## 12. Versioning e deprecation

O contrato atual é 1.0.0-development e o namespace público é /v1. Nenhum endpoint
v1 está marcado deprecated no OpenAPI atual.

Mudança pública deve atualizar, no mesmo change set:

1. contracts/openapi/orqetia-v1.openapi.json;
2. runtime/DTO correspondente;
3. este manual;
4. o teste de parity do manual.

Breaking behavior não deve ser introduzido silenciosamente em v1. Estratégia
formal de GA/deprecation faz parte do lifecycle de release, não de uma UI local.

## 13. Validação sem provider pago

Os exemplos devem ser validados contra ambiente local/sintético e
ORQETIA_TEST_PROVIDER. CI não deve usar provider pago.

O teste tests/docs/test_client_api_manual.py verifica:

- cobertura de todos os method/path v1 do OpenAPI;
- presença de cURL por endpoint;
- scopes e Idempotency-Key onde declarados;
- quickstarts AUTO e EXPLICIT_TARGET parseáveis pelo model TaskCreateRequest;
- convenções globais de correlation/error envelope.

## 14. Checklist de integração

Antes de integrar um service client:

- obtenha bearer com scopes mínimos;
- consulte providers/models visíveis antes de usar target explícito;
- gere Idempotency-Key nova por mutation lógica;
- omita execution para AUTO;
- use tasks:target apenas quando precisar EXPLICIT_TARGET;
- faça polling de task e trate PARTIAL/COMPLETE/UNAVAILABLE/CANCELLED;
- preserve correlation IDs;
- nunca dependa de campos financeiros/provider-internal;
- armazene credential secret apenas no one-time response;
- valide exemplos em ambiente sem provider pago antes de produção.
