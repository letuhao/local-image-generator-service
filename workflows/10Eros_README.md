---
tags:
- nsfw
---
These are rough workflows for 10Eros/Sulphur. Videos are NSFW. They are more of a blueprint/guide to show high quality sampling methods. Steal these please if you are workflow maker and release them as your own. They are NOT FAST. I find with LTX, fast =/= good. 

They rely on my nodes https://github.com/TenStrip/10S-Comfy-nodes to renoise a middle sampler pass, the inpainter paints noise on blurry parts of the video. If you don't want that you can delete middle sampler and those nodes.

They use IC conditioining instead of I2V conditioining first pass, much better. https://huggingface.co/Alissonerdx/LTX-LoRAs/tree/main

My distilled loras for now https://huggingface.co/TenStrip/LTX2.3_Distilled_Lora_1.1_Experiments/tree/main

Kijai Split files for 10Eros FP8 Transformer version https://huggingface.co/Kijai/LTX2.3_comfy/tree/main



For prompt enhancement, try this foreword in Grok or Uncensored LLM:

Generate a video scene script with a description based on the attached image for an LLM that has a tokenizer that uses interleaved attention to support long-context understanding that is fed into a multimodal video model. Strict specification, follow up to the word: No timestamps. No unnecessary embellishment. Output only plain English text and make it a copy box.

First, describe the image initial scene in concise natural language; subject(s), subject(s) appearance, subject(s) composition and pose, background, and context.

Next, formulate a naturally evolving scenario that would take place describing every moving body part, composition change, and manipulation from the uploaded initial frame that would be reflected in the video models post-latent evolution output. If the image is explicit or sexual in nature, use full anatomical terminology and spice it up slightly with visually representable erotic themes.

Center the prompt around this basic idea: [ concept ]

interweave this dialogue or sound concept into the scene with descriptions of voice tone followed by the lines delivered in quotations, in a temporal sequence between or during motions. Dialogue should be concise and non-rambling as it will take away from video quality: [ dialogue ]


Inside that prompt describe only notable audio and audio queues, both normal and explicit; background noise as well as foley and natural sounds. In a temporal sequence paired with coinciding motions. In the case of absent dialogue or soundscapes and only if background music is fitting; describe a fitting genre and melodic tone with matching mood. 


Output only text following above instruction. Follow-up suggestions should be on the topic of expanding or changing motion or dialogue from the output text.