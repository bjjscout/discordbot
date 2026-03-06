# subtitle_config.py

SUBTITLE_CONFIGS = {
    'reel': {
        'font': 'The Bold Font',
        'fontsize': 30,  # Font size for better visibility
        'color': '&HFFFFFF',  # White text
        'highlight_color': '&H00FFFF',  # Yellow highlight
        'max_chars': 30,  # Maximum characters per line
        'outline_color': '&H000000',  # Black outline
        'back_color': '&H000000',  # Black shadow
        'secondary_color': '&HFFFFFF',  # Secondary color
        'shadow_offset': 4,  # Shadow offset
        'text_position': 500,  # Position from top in pixels
        'alignment': 8,  # Top center alignment
        'bold': 1,  # Bold text
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
        'fontsize': 40,  # Font size for better visibility
        'color': '&HFFFFFF',  # White text
        'highlight_color': '&H00FFFF',  # Yellow highlight
        'max_chars': 30,  # Maximum characters per line
        'outline_color': '&H000000',  # Black outline
        'back_color': '&H000000',  # Black shadow
        'secondary_color': '&HFFFFFF',  # Secondary color
        'shadow_offset': 4,  # Shadow offset
        'text_position': 500,  # Position from top in pixels
        'alignment': 8,  # Top center alignment
        'bold': 1,  # Bold text
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
        'fontsize': 40,  # Font size for better visibility
        'color': '&HFFFFFF',  # White text
        'highlight_color': '&H00FFFF',  # Yellow highlight
        'max_chars': 30,  # Maximum characters per line
        'outline_color': '&H000000',  # Black outline
        'back_color': '&H000000',  # Black shadow
        'secondary_color': '&HFFFFFF',  # Secondary color
        'shadow_offset': 4,  # Shadow offset
        'text_position': 500,  # Position from top in pixels
        'alignment': 8,  # Top center alignment
        'bold': 1,  # Bold text
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
