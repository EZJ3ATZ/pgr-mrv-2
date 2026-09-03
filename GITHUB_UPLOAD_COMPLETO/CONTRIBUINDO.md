# Como subir código na Plataforma SST

Este arquivo descreve o fluxo de entrega. Ele existe porque três acidentes
repetidos custaram dias de produção rodando código errado:

1. Duas sessões editaram o mesmo arquivo na mesma pasta em disco e a segunda
   sobrescreveu a primeira sem aviso (27/08/2026).
2. Um push de arquivo inteiro a partir de disco defasado reverteu, em silêncio,
   um commit que já estava no `main` (03/07/2026).
3. Um script com lista fixa de caminhos deixou o `import_xlsx.py` 43 dias só no
   disco, enquanto a guarda que dependia dele já estava em produção. As três
   rotas de importação responderam 400 para qualquer planilha, inclusive a certa
   (descoberto em 17/08/2026).

Nenhum dos três foi erro de lógica. Foram falhas de processo de entrega.

## Regra única

Ninguém empurra no `main`. O `main` é protegido por ruleset: só entra por Pull
Request, e só depois que os dois jobs do CI ficam verdes. Não há revisão humana
obrigatória, então o gate é o teste, não a aprovação de ninguém.

## Subir uma entrega

```
py C:\Users\mathe\Downloads\entregar.py "fix: titulo curto" arquivo1.py pasta/arquivo2.py
```

O script faz, nesta ordem:

1. Atualiza um cache do repositório e mostra qual é o `main` atual.
2. Compara cada arquivo declarado com o `main`, byte a byte, e descarta os que
   não mudaram.
3. Varre os outros 200 arquivos versionados e avisa quais estão diferentes do
   `main` sem terem sido declarados. Isso costuma ser trabalho de outra sessão em
   andamento, e nada disso sobe.
4. Cria uma branch, copia apenas os arquivos declarados, commita e abre o PR.
5. Arma o merge automático. O PR entra no `main` sozinho quando o CI fica verde,
   e o Railway faz o deploy.

Opções úteis:

| Opção | Efeito |
|---|---|
| `--seco` | mostra o que faria e sai, sem criar branch nem PR |
| `--corpo "texto"` | corpo do commit e do PR: o porquê da mudança e o que foi validado |
| `--aguardar` | acompanha o CI até o merge ou a falha |
| `--sem-merge` | abre o PR e não arma o merge, quando a mudança precisa de decisão |
| `--reservados` | usa a lista de arquivos que esta sessão reservou |

Use `--sem-merge` quando a mudança mexe em motor de cálculo, migração de banco,
cobrança ou disparo de e-mail em massa. Nesses casos, quem decide é o Bernardo.

## Reserva de arquivo entre sessões

Várias sessões Claude editam a mesma pasta em disco, e essa pasta não é um clone
git. Git não protege nada ali: a segunda escrita simplesmente apaga a primeira.

Por isso existe uma reserva. A primeira sessão que edita um arquivo o reserva, e
qualquer outra sessão é barrada naquele arquivo até a primeira terminar. A
reserva é automática, feita por um hook antes de cada edição, e expira após três
horas sem uso.

```
py C:\Users\mathe\.claude\hooks\reserva_arquivo.py --listar
py C:\Users\mathe\.claude\hooks\reserva_arquivo.py --liberar <id-da-sessao>
```

Libere uma reserva à mão só quando tiver certeza de que a sessão dona já
terminou.

## Duas regras que vieram de acidente

Guarda de validação e o código que produz a chave lida por ela sobem no mesmo
commit. Foi a separação dos dois que gerou o acidente 3 acima, e o sintoma
enganava: parecia planilha errada, era meia entrega.

Trabalho que não pode ir para produção agora não fica salvo nesta pasta. Como
qualquer sessão pode declarar qualquer arquivo, código pela metade em disco é
código que pode subir por engano.

## O que foi aposentado

`push_todos.py` e `push_github.py` não devem mais ser usados. Os dois sobem
arquivos inteiros a partir do disco, sem comparar com o `main` e sem mostrar
diff, e foram a causa direta dos acidentes 2 e 3.
