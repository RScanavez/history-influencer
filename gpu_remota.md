# GPU remota (RunPod)

Configuração da GPU alugada. Enquanto `pod-id` estiver vazio, a opção "Remota" fica desativada no painel e tudo
roda na GPU local. A chave da API fica em `secrets/runpod.txt` e a chave SSH do túnel em `secrets/runpod_ssh`
(as duas fora do git; nunca cole no chat).

Cada lote novo escolhe a GPU no painel (Novo lote → GPU). Vídeos criados antes disso continuam na GPU local.
O painel liga a máquina quando entra um job remoto e fala com ela por um túnel SSH (o ComfyUI remoto não fica
exposto na internet). A máquina NÃO é desligada sozinha (decisão do usuário, 2026-10-03): o painel avisa quando ela
está ligada sem uso e mostra o botão "Desligar GPU remota". `desligar-automatico: sim` reativa o desligamento após
`desligar-apos-min` minutos sem jobs.

- pod-id:
- volume-id:
- datacenter: EUR-NO-2
- gpu: NVIDIA H100 80GB HBM3
- porta-local: 8189
- wan-alto: wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors
- wan-baixo: wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors
- fun-alto: wan2.2_fun_control_high_noise_14B_fp8_scaled.safetensors
- fun-baixo: wan2.2_fun_control_low_noise_14B_fp8_scaled.safetensors
- desligar-automatico: sim
- desligar-apos-min: 3

## Como configurar numa máquina nova

O painel confere tudo isto ao abrir (seção "Verificação do ambiente") e diz o que falta.

1. **Conta e crédito**: crie a conta em runpod.io, ative a verificação em duas etapas e coloque crédito em
   Billing (deixe o Auto-pay desligado se quiser limitar o gasto ao saldo).
2. **Chave da API**: runpod.io → Settings → API Keys → Create API Key (nome `influencer-painel`, permissão All).
   Salve só a chave, numa linha, em `secrets/runpod.txt` (crie a pasta `secrets` na raiz do projeto; ela fica fora
   do git). Nunca cole a chave em chat ou e-mail.
3. **Chave SSH do túnel**: no terminal, na pasta do projeto:
   `ssh-keygen -t ed25519 -f secrets/runpod_ssh -N ""`
   Depois abra `secrets/runpod_ssh.pub`, copie a linha inteira (começa com `ssh-ed25519`) e cole em
   runpod.io → Settings → SSH Public Keys → Save.
4. **Disco e máquina**: crie um Network Volume (~150 GB; o deste projeto passou de 100 para 150 GB em 2026-10-05) num datacenter que tenha a GPU desejada e uma máquina
   (Pod, nuvem segura) ligada a esse volume em `/workspace`, com a porta `22/tcp`, a variável `PUBLIC_KEY` com o
   conteúdo de `secrets/runpod_ssh.pub` e o comando de início
   `bash -c 'bash /workspace/start_comfyui.sh >> /workspace/comfyui-start.log 2>&1 & /start.sh'`.
   Com a chave da API salva, o Claude faz este passo por você.
5. **Instalação**: copie `scripts/remoto/instalar_comfyui.sh` para `/workspace` na máquina e rode-o (instala o
   ComfyUI, os complementos e os modelos nas mesmas versões da instalação local; ~40 min).
6. **Configuração**: preencha `pod-id`, `volume-id` e `datacenter` acima e clique em "Verificar de novo" no painel.
