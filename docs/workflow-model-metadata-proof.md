# Workflow model metadata proof

Date: 2026-07-31

Environment under proof:

- ComfyUI Core: `0.29.0`
- ComfyUI frontend: `1.47.10`
- Python: `3.13.12`
- implementation commit: `bda93d935557c3f1904bf76b0c12be79a431e1b1`

## Native missing-model visibility

The native missing-model panel recognized the synthetic fixture entry
`cloud-run-native-proof.safetensors` in `diffusion_models` and displayed the
`Download All` action. The action was not used. The exact model was absent
before and after the check, so this proof created no model bytes.

## Gold preflight

The first capture failed closed with `mapping_required` because the live
workflow did not contain native model source records. The missing producer
contract is an exact `properties.models` record on each active loader,
containing `name`, `directory`, and a public Hugging Face `url`; an optional
digest must use `hash_type: sha256` and `hash`.

After the user supplied the source mapping, the five records were added only
to the isolated in-memory proof canvas. The original workflow was neither
exported nor executed. Normal Cloud Run canvas capture followed by the free
preflight produced these verified values:

| Model | Repository and file path | Immutable revision | Exact bytes | SHA-256 | Canonical destination |
| --- | --- | --- | ---: | --- | --- |
| `flux1-fill-dev.safetensors` | `Comfy-Org/flux1-dev` — `split_files/diffusion_models/flux1-fill-dev.safetensors` | `0f6b956e6e2e041fb73d079b72ec0e761506f601` | 23,804,922,408 | `03e289f530df51d014f48e675a9ffa2141bc003259bf5f25d75b957e920a41ca` | `models/diffusion_models/flux1-fill-dev.safetensors` |
| `clip_l.safetensors` | `comfyanonymous/flux_text_encoders` — `clip_l.safetensors` | `6af2a98e3f615bdfa612fbd85da93d1ed5f69ef5` | 246,144,152 | `660c6f5b1abae9dc498ac2d21e1347d2abdb0cf6c0c0c8576cd796491d9a6cdd` | `models/text_encoders/clip_l.safetensors` |
| `t5xxl_fp8_e4m3fn.safetensors` | `comfyanonymous/flux_text_encoders` — `t5xxl_fp8_e4m3fn.safetensors` | `6af2a98e3f615bdfa612fbd85da93d1ed5f69ef5` | 4,893,934,904 | `7d330da4816157540d6bb7838bf63a0f02f573fc48ca4d8de34bb0cbfd514f09` | `models/text_encoders/t5xxl_fp8_e4m3fn.safetensors` |
| `ae.safetensors` | `Comfy-Org/Lumina_Image_2.0_Repackaged` — `split_files/vae/ae.safetensors` | `22e393d707f2d13e736b1a461c958644258cd9d9` | 335,304,388 | `afc8e28272cd15db3919bacdb6918ce9c1ed22e96cb12c4d5ed0fba823529e38` | `models/vae/ae.safetensors` |
| `4x_foolhardy_Remacri.pth` | `fofr/comfyui` — `upscale_models/4x_foolhardy_Remacri.pth` | `0cd0e3e76111f1f2e6f25091581958f381a2357e` | 67,025,055 | `e1a73bd89c2da1ae494774746398689048b5a892bd9653e146713f9df8bca86a` | `models/upscale_models/4x_foolhardy_Remacri.pth` |

All five model rows were `resolved` from Hugging Face. The active file-backed
input was reported only as `verified local input` and was also `resolved`.
Its identity, path, size, and digest are intentionally omitted.

The exact deduplicated transfer total was **29,347,331,113 bytes**, equal to
the artifacts in the preflight result. The five selected model files remained
absent from local model roots after the preflight.

## Safety boundary

This proof performed no model download, workflow execution, offer search,
Vast mutation, instance creation, GPU use, paid charge, R2 mutation, template
mutation, release mutation, or worker release publication. It records no
captured workflow JSON, local path, credential, request header, settings
payload, database content, or private input identity.

The run is stopped at the paid boundary. A Gold GPU test awaits separate
authorization stating the maximum instance count, maximum hourly price, and
absolute maximum duration or total cost.
