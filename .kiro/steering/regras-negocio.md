# Regras de Negocio

## Termos e Conceitos

| Termo | Significado |
|-------|-------------|
| AWB | Air Waybill - documento de carga aerea (11 digitos, comeca com 127) |
| CTe | Conhecimento de Transporte eletronico (vinculado ao AWB) |
| MDF-e | Manifesto de Documentos Fiscais eletronico (agrupa CTes de um voo) |
| TA / TADe | Termo de Apreensao / Termo de Averiguacao (emitido pela SEFAZ) |
| FRAP | Frete pago na entrega (Frete a Pagar) |
| RETIRA | Carga retirada pelo cliente no terminal |
| ENTREGA | Carga entregue no endereco do cliente (domicilio) |
| Uncleared | Cargas pendentes de baixa no sistema |

## Regras de Liberacao de Voos

1. **So libera AWBs da secao RETIRA** (nunca domicilio/entrega)
2. **AWBs com termo SEFAZ = RETIDOS** (nunca libera)
3. **Se CTe com termo nao foi mapeado para AWB → NAO libera NENHUM AWB do voo** (seguranca)
4. **AWBs MELI nao sao liberados** (servico especial, tratamento diferente)
5. **Se SEFAZ inconclusivo (relatorio nao extraido) → NAO libera o voo** (consultar manualmente)
6. **Verificacao final**: apos liberar, confirma que AWBs com termo continuam retidos

## Prioridade de verificacao SEFAZ

1. **Email primeiro** (Outlook): se PF Central respondeu
   - "sem termos" / "liberado" → confia, libera tudo RETIRA
   - "gerado relatorio anexo" → baixa PDF, verifica termos
2. **PDF do email**: se mostra TOTAL DE TERMOS 0 → voo liberado (confiavel)
3. **Site SEFAZ** (fallback): so consulta se email nao resolveu
   - TAs emitidos = 0 na tabela → libera sem precisar clicar Imprimir
   - TAs > 0 → clica Imprimir Relatorio (timeout 100s)
   - Se timeout → NAO libera (marca erro "consultar manualmente")

## Regras de Uncleared

- **Lista 1** (pistolados): AWBs fisicamente presentes no terminal = NAO BAIXAR
- **Lista 2** (planilha): AWBs cobrados pelo sistema pra dar baixa
- **Resultado**: AWBs da planilha que NAO estao no terminal = podem ser investigados/baixados
- Motivos comuns de uncleared: entregue sem baixar, FRAP sem pagamento, lote parcial, carga nao recebida no sistema

## Regras de Seguranca

- NUNCA liberar um voo sem certeza de que esta livre de termos
- NUNCA commitar credenciais (ficam em %APPDATA%/automacao_sefaz/credenciais.json)
- Se em duvida, nao libera — marca pra verificacao manual
