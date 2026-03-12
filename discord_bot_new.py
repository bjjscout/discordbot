    cogs_to_load = [
        'video',      # !process_sheet only (process_video disabled)
        # 'twitter',    # Disabled - !tweet, !tweetsheet
        # 'instagram',  # Disabled - !ig, !igmake
        # 'raptive',   # Disabled - !rapcalf, !rapdoc
        # 'scripts',   # Disabled - !aiwriter, !flux (old subprocess version)
        'writer',     # !aiwriter, !ytwriter - refactored with OpenAI only
        'summarization', # !sum, !sum2, !sumw
        'utility',    # Utility commands
        # 'webhooks',  # Disabled - Webhook triggers
        'whisper',    # !whisper - WhisperX API transcription
    ]