# subtitle_config.py

SUBTITLE_CONFIGS = {
    'reel': {
        'font': 'The Bold Font',
        'fontsize': 100,  # Match ClipGenius
        'color': '&HFFFFFF',  # White text
        'highlight_color': '&H00FFFF',  # Yellow highlight
        'max_chars': 30,  # Maximum characters per line
        'outline_color': '&H000000',  # Black outline
        'back_color': '&H000000',  # Black shadow
        'secondary_color': '&HFFFFFF',  # Secondary color
        'shadow_offset': 4,  # Shadow offset
        'text_position': 384,  # Match ClipGenius: 20% from bottom (1920 * 0.20 = 384)
        'alignment': 2,  # Center alignment (ClipGenius uses 2)
        'bold': 1,  # Bold text
        'outline': 3,  # ClipGenius uses 3
        'logo': {
            'url': {
                'calf': 'https://calfkicker.com/wp-content/uploads/2023/12/calfkicker_new_font.png',
                'doc': 'https://bjjdoc.com/wp-content/uploads/2024/07/doc_opacity_80.png'
            },
            'size_factor': 0.4,  # 40% of video width
            'logo_y_position_factor': 0.95,  # 5% from the bottom.
            'opacity': 0.6
        }
    },

    'landscape': {
        'font': 'The Bold Font',
        'fontsize': 120,  # Larger for better visibility on 1080p
        'color': '&HFFFFFF',  # White text
        'highlight_color': '&H00FFFF',  # Yellow highlight
        'max_chars': 30,  # Maximum characters per line
        'outline_color': '&H000000',  # Black outline
        'back_color': '&H000000',  # Black shadow
        'secondary_color': '&HFFFFFF',  # Secondary color
        'shadow_offset': 4,  # Shadow offset
        'text_position': 80,  # Higher position - 7% from bottom on 1080 height
        'alignment': 2,  # Bottom center alignment
        'bold': 1,  # Bold text
        'outline': 3,  # ClipGenius uses 3
            'logo': {
                'url': {
                        'calf': 'https://calfkicker.com/wp-content/uploads/2023/12/calfkicker_new_font.png',
                        'doc': 'https://bjjdoc.com/wp-content/uploads/2024/07/doc_opacity_80.png'
                    },
            'size_factor': 0.2,  # 40% of video width
            'logo_y_position_factor': 0.9,  # 7% from the top of the video (e.g., to -2%) or lower if you want them higher in relation with each other e.g.-3% for a better separation between subtitles and logo etc...   # New parameter added here based on requirement/design preference
            'opacity': 0.6
        }
    },   


    'square': {
        'font': 'The Bold Font',
        'fontsize': 100,  # Larger for better visibility on 1080x1080
        'color': '&HFFFFFF',  # White text
        'highlight_color': '&H00FFFF',  # Yellow highlight
        'max_chars': 30,  # Maximum characters per line
        'outline_color': '&H000000',  # Black outline
        'back_color': '&H000000',  # Black shadow
        'secondary_color': '&HFFFFFF',  # Secondary color
        'shadow_offset': 4,  # Shadow offset
        'text_position': 80,  # Higher position - 7% from bottom on 1080 height
        'alignment': 2,  # Bottom center alignment
        'bold': 1,  # Bold text
        'outline': 3,  # ClipGenius uses 3
        'logo': {
            'url': {
                    'calf': 'https://calfkicker.com/wp-content/uploads/2023/12/calfkicker_new_font.png',
                    'doc': 'https://bjjdoc.com/wp-content/uploads/2024/07/doc_opacity_80.png'
                },
            'size_factor': 0.4,  # 40% of video width
            'logo_y_position_factor': 0.9,  # 5% from the bottom
            'opacity': 0.6
        }
    }
}
