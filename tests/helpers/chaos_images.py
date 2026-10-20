"""Synthetic card layouts made from the installed pixels, with moving anchors."""
import numpy as np
import cv2


def auto_icon_image(base, angle):
    """Keep the centre glyph stationary while rotating only the surrounding ring."""
    height, width = base.shape[:2]
    y, x = np.indices((height, width))
    image = cv2.warpAffine(base, cv2.getRotationMatrix2D((width/2, height/2), angle, 1.),
                           (width, height))
    centre = np.hypot(x-width/2, y-height/2) < 23
    image[centre] = base[centre]
    return image


def loot_frame(config, templates, keys, *, shift=(0, 0)):
    frame = np.zeros((1306,2322,3),np.uint8)
    anchor = templates.get(config.chaos.scenes['loot_reroll_anchor'].template_key).image
    for slot,key in enumerate(keys):
        x,y = round(548+580.5*slot)+shift[0],987+shift[1]
        frame[y:y+anchor.shape[0],x:x+anchor.shape[1]] = anchor
        data = templates.get(config.chaos.scenes[key].template_key).image
        dx,dy = config.chaos.loot_recognition['template_regions'][key]['origin']
        frame[y+dy:y+dy+data.shape[0],x+dx:x+dx+data.shape[1]] = data
    return frame
