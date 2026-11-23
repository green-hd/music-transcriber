# Transcritor de músicas no terminal

Transcreve a voz da música, salva a letra e as legendas e pode tocar o áudio mostrando as palavras no terminal. Funciona com caminhos do Windows e Linux, inclusive nomes com espaços e acentos.

## Instalação

Recomendado: Python 3.11 ou 3.12, 64 bits. A transcrição usa a CPU; não exige placa NVIDIA, Ollama, PyTorch ou instalação separada do FFmpeg. O faster-whisper decodifica o áudio com PyAV.

Crie um ambiente novo para instalar somente o necessário. O ambiente `.venv` antigo não é alterado e pode conter pacotes pesados que este aplicativo não usa mais.

Linux:

```sh
python3 -m venv .venv-leve
.venv-leve/bin/python -m pip install -r requirements-playback.txt
.venv-leve/bin/python transcriber.py
```

Windows (PowerShell ou Prompt de Comando, sem precisar ativar o ambiente):

```powershell
py -3 -m venv .venv-leve
.venv-leve\Scripts\python.exe -m pip install -r requirements-playback.txt
.venv-leve\Scripts\python.exe transcriber.py
```

No Linux, se a criação do ambiente informar que falta `venv`, instale o pacote `python3-venv` pelo gerenciador da sua distribuição.

Para **somente transcrever**, instale `requirements.txt` em vez de `requirements-playback.txt` e execute com `--sem-reproducao`. Nesse modo, pygame e dispositivo de áudio são dispensáveis.

Os exemplos abaixo usam `python`; substitua pelo caminho do Python do ambiente criado acima ou ative o ambiente antes.

## Uso

### Player em janela (opcional)

Depois de instalar `requirements-playback.txt`, abra a interface:

Linux:

```sh
.venv/bin/python player.py
```

Windows:

```powershell
.venv\Scripts\python.exe player.py
```

Se criou o ambiente `.venv-leve` da instalação acima, substitua `.venv` por `.venv-leve` nesses comandos.

A janela tem biblioteca de músicas, botão para abrir arquivos, seleção de qualidade/idioma, contexto, volume (inicialmente 10%), pausa e retomada, progresso e letra aparecendo caractere por caractere. Selecione uma faixa e clique em **Preparar & tocar**. A transcrição ocorre em outro processo, mantendo a janela disponível, e usa os mesmos resultados/cache do terminal quando a configuração coincide. O detalhe do download também aparece no terminal. A barra mostra o progresso da reprodução; nesta versão ela não permite avançar a faixa.

O player usa Tkinter e pygame, sem bibliotecas adicionais de interface. No Ubuntu/Debian, caso falte Tkinter, instale `python3-tk`. É preciso uma sessão gráfica e uma saída de áudio disponível. Fechar a janela encerra a reprodução e o processo de transcrição que ela iniciou. O modo terminal continua disponível normalmente.

### Terminal

```sh
# Menu de músicas
python transcriber.py

# Arquivo específico: use aspas para nomes com espaços
python transcriber.py "musicas/Loving Machine.mp3"

# Inglês, maior precisão (mais lento e maior consumo de memória)
python transcriber.py "musicas/Loving Machine.mp3" --idioma en --preciso

# Orientar nomes próprios, sem substituir a transcrição por uma letra pronta
python transcriber.py "musicas/Loving Machine.mp3" --idioma en --contexto "Maddie Klein, fabulous loving machine." --volume 0.15

# Priorizar consumo menor, aceitando mais erros de reconhecimento
python transcriber.py "musicas/Loving Machine.mp3" --leve

# Português, sem reprodução
python transcriber.py "musicas/minha faixa.mp3" --idioma pt --sem-reproducao

# Menor modelo e menos uso de CPU, com possível perda de precisão
python transcriber.py "musicas/Loving Machine.mp3" --modelo tiny --threads 2

# Usar um modelo já baixado sem acesso à internet
python transcriber.py "musicas/Loving Machine.mp3" --modelo small --offline

# Reprocessar em vez de usar o cache
python transcriber.py "musicas/Loving Machine.mp3" --refazer

python transcriber.py --help
```

Coloque novas músicas em `musicas/`. O menu também encontra os arquivos existentes na raiz do projeto; nenhuma música é movida automaticamente. `--pasta "outra pasta"` troca a pasta pesquisada, e o caminho direto permite arquivos de qualquer diretório. Os caminhos padrão são relativos ao script, então ele também funciona se for chamado de outro diretório.

Formatos aceitos para transcrição: MP3, WAV, FLAC, M4A, OGG, OPUS, AAC e WMA. A reprodução depende dos codecs disponíveis no pygame/SDL do sistema; se falhar, os resultados já estarão salvos. Ctrl+C encerra a execução e libera o dispositivo de áudio.

## Resultados e cache

Os arquivos ficam em `transcricoes/` (ou na pasta escolhida com `--saida`):

- `.txt`: texto da letra, separado por segmentos reconhecidos.
- `.srt`: legendas com início e fim de cada segmento.
- `.lrc`: letra sincronizada por segmento, para players compatíveis.
- `.json`: segmentos, palavras com tempos, idioma e dados do cache.

O nome contém parte do hash do áudio e da configuração, para distinguir músicas com o mesmo nome e modelos/idiomas diferentes. Repetir o mesmo comando reutiliza o JSON e recria as exportações, sem carregar o modelo. Alterar o áudio, modelo, idioma, contexto ou modo de precisão gera outro resultado. Um cache inválido é refeito. O hash é calculado lendo o áudio em blocos, sem carregar o arquivo inteiro na memória.

## Consumo e qualidade

O padrão é **small / CPU / int8**, com até quatro threads e busca de cinco hipóteses por etapa. O antigo padrão base com busca simples errava bastante em letras cantadas. `--leve` oferece esse modo mais econômico; `--preciso` usa **medium**, que exige mais memória e tempo. `--modelo` permite escolher explicitamente outro tamanho, inclusive junto com esses modos. O modelo é liberado antes da reprodução; o aplicativo não consulta outro modelo para reescrever palavras.

O nome do arquivo é fornecido como contexto ao reconhecimento. `--contexto` acrescenta nomes próprios e expressões, como "Maddie Klein"; isso ajuda, mas não força o modelo a reproduzir a letra correta. A mudança de configuração invalida o uso dos resultados da versão anterior, sem apagar seus arquivos. O programa transcreve no idioma original; não faz tradução.

Ao começar a reprodução, o terminal limpa uma vez e mostra um gato ASCII maior, a assinatura **by green** abaixo e o nome da música. A letra aparece caractere por caractere, distribuída pelos tempos de cada palavra reconhecida e com intervalo máximo de 60 ms entre caracteres. A sincronização usa o relógio da reprodução, sem somar atrasos entre palavras. Os tempos reconhecidos podem ser imprecisos; se a saída atrasar, ela acompanha a posição atual do áudio.

O idioma é detectado automaticamente. Se souber o idioma, informar `--idioma en`, `--idioma pt` etc. pode ajudar. A primeira execução de cada modelo precisa de internet para baixar os arquivos; depois eles ficam no cache do Hugging Face, fora da pasta de transcrições. `--offline` impede downloads e exige o modelo já disponível.

O download mostra as barras de progresso padrão do Hugging Face. O modelo `medium` de `--preciso` tem aproximadamente 1,53 GB para baixar; usar int8 reduz o consumo durante a execução, não o tamanho desse download. O aviso "unauthenticated requests" não é um erro: o token é opcional para esses modelos públicos. Arquivos já disponíveis no cache são reutilizados.

Música com instrumentos altos, voz sobreposta, efeitos ou pronúncia pouco clara pode gerar erros e palavras em trechos instrumentais. Nenhum destes modelos garante uma letra exata. O filtro de voz fica desligado para evitar cortar canto; a configuração reduz repetição e tenta descartar alucinações em silêncio. Para comparar resultados, use `--idioma` e `--preciso` e confira o texto com o áudio.

## Verificação

Os testes de regressão não precisam baixar modelos nem ter dispositivo de áudio:

```sh
python -m unittest discover -s tests -v
```

Referências das dependências: [faster-whisper](https://github.com/SYSTRAN/faster-whisper) e [reprodução com pygame](https://www.pygame.org/docs/ref/music.html).

## Fontes, ênfase e revisão da transcrição

Em **Configurações** no player, escolha **Serifada**, **Limpa** ou **Monoespaçada**. O aplicativo procura uma fonte instalada no Windows/Linux e usa uma alternativa se ela não existir. A fonte padrão é **Limpa**. A opção **Ênfase** pode ser ligada durante a reprodução para destacar palavras mais intensas em negrito e verde.

O destaque é uma aproximação pela energia do áudio entre 150 e 4000 Hz, comparando cada palavra com a música e com os segundos próximos. A análise usa blocos de 50 ms, sem outro modelo de IA nem manter o áudio inteiro na memória. O volume do player não altera os destaques. Instrumentos na mesma faixa de frequência também influenciam: o efeito não detecta emoção nem separa a voz. Se a análise falhar, a reprodução continua sem o efeito.

Para comparar o reconhecimento, o seletor de qualidade também oferece:

- **Inglês · medium.en**: modelo específico para inglês; força o idioma inglês.
- **Máxima · large-v3**: modelo maior, com download, memória e processamento mais exigentes. É uma opção para experimentar; não garante a letra correta.

No terminal, essas opções correspondem a `--modelo medium.en --idioma en` e `--modelo large-v3`. Modelos `.en` não devem ser usados para músicas em outros idiomas. No player, marque **Refazer** para ignorar uma transcrição em cache; mudar o modelo ou o contexto já gera outro resultado automaticamente.

Informar o idioma e nomes próprios no contexto continua ajudando. Para melhorar a voz em músicas com instrumentos muito altos, uma etapa futura seria separar os vocais antes de reconhecer; ela exigiria outro modelo e mais recursos, por isso não foi adicionada à configuração leve.

## Alongamento e bolinha de karaokê

Em **Configurações** há duas opções independentes:

- **Alongar palavras**: palavras com duração reconhecida longa ganham até três repetições da última letra durante a exibição, como `rejectionnnn`. Vem desligada. É uma estimativa visual baseada no tempo da palavra, não uma identificação da vogal sustentada. A transcrição, o cache e os arquivos exportados continuam com a grafia original.
- **Bolinha karaokê**: um indicador verde acompanha a palavra cantada, acima das letras. Funciona junto com a digitação por caracteres, pausa com o áudio e some nos intervalos entre palavras. Vem desligada.

As duas opções podem ser alteradas enquanto a música toca ou está pausada, sem transcrever novamente. A precisão dos efeitos depende dos timestamps do Whisper; palavras com tempos incorretos podem parecer alongadas ou fora de sincronia. As animações não exigem outro modelo nem downloads adicionais.

## Player sem internet

Abra `python player.py`, abra **Configurações** e marque **Offline**, escolha a faixa e clique em **Preparar & tocar**. Nesse modo, o reconhecimento recebe `--offline` e usa somente arquivos locais, sem consultar o Hugging Face. O modelo da qualidade escolhida precisa ter sido baixado anteriormente, a menos que já exista uma transcrição em cache para a mesma música e configuração. Se faltar o modelo, desmarque Offline e prepare a faixa com internet uma vez. As fontes, animações, análise de intensidade e reprodução funcionam localmente.
