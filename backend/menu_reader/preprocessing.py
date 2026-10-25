"""Measured OCR preprocessing candidates. No character substitution or generative repair.

Profiles preserve an explicit inverse scale into the original upright photograph. `native`
is the unchanged baseline; candidate profiles are opt-in until their comparison is reviewed.
"""
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

PROFILES = ('native', 'upscale2', 'upscale2_contrast', 'upscale2_sharp', 'adaptive')


def prepare(image, profile):
    if profile not in PROFILES:
        raise ValueError('Unknown OCR preprocessing profile')
    original = image.convert('RGB')
    # Upsampling already high-resolution photographs adds cost, not new detail. The real
    # small-text test uses native-resolution photos/crops; this limit is recorded per run.
    scale = 2 if profile != 'native' and max(original.size) <= 2000 else 1
    result = original.resize((original.width*scale, original.height*scale), Image.Resampling.LANCZOS) if scale!=1 else original.copy()
    if profile in ('upscale2_contrast','adaptive'):
        result = ImageOps.autocontrast(ImageOps.grayscale(result), cutoff=.5).convert('RGB')
    if profile=='upscale2_sharp':
        result = result.filter(ImageFilter.UnsharpMask(radius=1,percent=150,threshold=3))
    if profile=='adaptive':
        import cv2
        import numpy as np
        gray=np.array(ImageOps.grayscale(result))
        result=Image.fromarray(cv2.adaptiveThreshold(gray,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                                   cv2.THRESH_BINARY,31,9)).convert('RGB')
    return result, dict(profile=profile,scaleX=result.width/original.width,scaleY=result.height/original.height,
                       sourceUprightSize=list(original.size),recognitionSize=list(result.size),
                       inverse='source_x = recognition_x / scaleX; source_y = recognition_y / scaleY',
                       textCorrectionApplied=False)
