# coding: utf-8
import os
import re

from django.conf import settings as project_settings

from dext.utils.app_settings import app_settings

SITE_SECTIONS = ( (re.compile(r'^/$'), 'index'),
                  (re.compile(r'^/news.*$'), 'news'),
                  (re.compile(r'^/forum.*$'), 'forum'),
                  (re.compile(r'^/accounts/profile.*$'), 'profile'),
                  (re.compile(r'^/game/heroes.*$'), 'hero'),
                  (re.compile(r'^/game.*$'), 'game'),
                  (re.compile(r'^/manual.*$'), 'manual') )

portal_settings = app_settings('PORTAL',
                               DUMP_EMAIL='admin@the-tale.org',
                               META_CONFIG=os.path.join(project_settings.PROJECT_DIR, 'meta_config.json'),
                               NEWS_ON_INDEX=3)
