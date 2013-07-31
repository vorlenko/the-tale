# coding: utf-8
import uuid

from django.core.urlresolvers import reverse
from django.contrib.auth import logout as django_logout
from django.utils.log import getLogger

from dext.views import handler, validator, validate_argument
from dext.utils.urls import UrlBuilder, full_url

from common.postponed_tasks import PostponedTaskPrototype
from common.utils.resources import Resource
from common.utils.pagination import Paginator
from common.utils.decorators import login_required

from blogs.models import Post as BlogPost, POST_STATE as BLOG_POST_STATE

from game.heroes.models import Hero
from game.heroes.prototypes import HeroPrototype

from accounts.friends.prototypes import FriendshipPrototype
from accounts.personal_messages.prototypes import MessagePrototype

from accounts.prototypes import AccountPrototype, ChangeCredentialsTaskPrototype, AwardPrototype, ResetPasswordTaskPrototype
from accounts.postponed_tasks import RegistrationTask
from accounts.models import CHANGE_CREDENTIALS_TASK_STATE
from accounts import forms
from accounts.conf import accounts_settings
from accounts.logic import logout_user, login_user, get_system_user
from accounts.workers.environment import workers_environment as infrastructure_workers_environment

logger = getLogger('django.request')

@validator(code='common.fast_account', message=u'Вы не закончили регистрацию и данная функция вам не доступна')
def validate_fast_account(self, *args, **kwargs): return not self.account.is_fast

@validator(code='common.ban_forum', message=u'Вам запрещено проводить эту операцию')
def validate_ban_forum(self, *args, **kwargs): return not self.account.is_ban_forum

@validator(code='common.ban_game', message=u'Вам запрещено проводить эту операцию')
def validate_ban_game(self, *args, **kwargs): return not self.account.is_ban_game


class RegistrationResource(Resource):

    @handler('fast', method='post')
    def fast(self):

        if self.account.is_authenticated():
            return self.json_error('accounts.registration.fast.already_registered', u'Вы уже зарегистрированы')

        if accounts_settings.SESSION_REGISTRATION_TASK_ID_KEY in self.request.session:

            task_id = self.request.session[accounts_settings.SESSION_REGISTRATION_TASK_ID_KEY]
            task = PostponedTaskPrototype.get_by_id(task_id)

            if task is not None:
                if task.state.is_processed:
                    return self.json_error('accounts.registration.fast.already_processed', u'Вы уже зарегистрированы, обновите страницу')
                if task.state.is_waiting:
                    return self.json_processing(task.status_url)
                # in other case create new task

        referer = None
        if accounts_settings.SESSION_REGISTRATION_REFERER_KEY in self.request.session:
            referer = self.request.session[accounts_settings.SESSION_REGISTRATION_REFERER_KEY]

        referral_of_id = None
        if accounts_settings.SESSION_REGISTRATION_REFERRAL_KEY in self.request.session:
            referral_of_id = self.request.session[accounts_settings.SESSION_REGISTRATION_REFERRAL_KEY]

        registration_task = RegistrationTask(account_id=None, referer=referer, referral_of_id=referral_of_id)

        task = PostponedTaskPrototype.create(registration_task,
                                             live_time=accounts_settings.REGISTRATION_TIMEOUT)

        self.request.session[accounts_settings.SESSION_REGISTRATION_TASK_ID_KEY] = task.id

        infrastructure_workers_environment.registration.cmd_task(task.id)

        return self.json_processing(task.status_url)


class AuthResource(Resource):

    @handler('login', method='get')
    def login_page(self, next_url='/'):
        if self.account.is_authenticated():
            return self.redirect(next_url)

        login_form = forms.LoginForm()
        return self.template('accounts/login.html',
                             {'login_form': login_form,
                              'next_url': next_url} )

    @handler('login', method='post')
    def login(self, next_url='/'):
        login_form = forms.LoginForm(self.request.POST)

        if login_form.is_valid():

            account = AccountPrototype.get_by_email(login_form.c.email)
            if account is None:
                return self.json_error('accounts.auth.login.wrong_credentials', u'Неверный логин или пароль')

            if not account.check_password(login_form.c.password):
                return self.json_error('accounts.auth.login.wrong_credentials', u'Неверный логин или пароль')

            login_user(self.request, nick=account.nick, password=login_form.c.password)

            return self.json_ok(data={'next_url': next_url})

        return self.json_error('accounts.auth.login.form_errors', login_form.errors)

    @handler('logout', method=['post'])
    def logout_post(self):
        logout_user(self.request)
        return self.json_ok()

    @handler('logout', method=['get'])
    def logout_get(self):
        django_logout(self.request)
        self.request.session.flush()
        return self.redirect('/')


class ProfileResource(Resource):

    @login_required
    @handler('', name='show', method='get')
    def profile(self):
        data = {'email': self.account.email if self.account.email else u'укажите email',
                'nick': self.account.nick if not self.account.is_fast and self.account.nick else u'укажите ваше имя'}
        edit_profile_form = forms.EditProfileForm(data)

        settings_form = forms.SettingsForm({'personal_messages_subscription': self.account.personal_messages_subscription})

        return self.template('accounts/profile.html',
                             {'edit_profile_form': edit_profile_form,
                              'settings_form': settings_form} )

    @login_required
    @handler('edited', name='edited', method='get')
    def edit_profile_done(self):
        return self.template('accounts/profile_edited.html')

    @login_required
    @handler('confirm-email-request', method='get')
    def confirm_email_request(self):
        return self.template('accounts/confirm_email_request.html')

    @login_required
    @handler('update', name='update', method='post')
    def update_profile(self):

        edit_profile_form = forms.EditProfileForm(self.request.POST)

        if edit_profile_form.is_valid():

            if self.account.is_fast and not (edit_profile_form.c.email and edit_profile_form.c.password and edit_profile_form.c.nick):
                return self.json_error('accounts.profile.update.empty_fields', u'Необходимо заполнить все поля')

            if edit_profile_form.c.email:
                existed_account = AccountPrototype.get_by_email(edit_profile_form.c.email)
                if existed_account and existed_account.id != self.account.id:
                    return self.json_error('accounts.profile.update.used_email', {'email': [u'На этот адрес уже зарегистрирован аккаунт']})

            if edit_profile_form.c.nick:
                existed_account = AccountPrototype.get_by_nick(edit_profile_form.c.nick)
                if existed_account and existed_account.id != self.account.id:
                    return self.json_error('accounts.profile.update.used_nick', {'nick': [u'Это имя уже занято']})

            task = ChangeCredentialsTaskPrototype.create(account=self.account,
                                                         new_email=edit_profile_form.c.email,
                                                         new_password=edit_profile_form.c.password,
                                                         new_nick=edit_profile_form.c.nick,
                                                         relogin_required=True)

            postponed_task = task.process(logger)

            if postponed_task is not None:
                return self.json_processing(postponed_task.status_url)

            return self.json_ok(data={'next_url': reverse('accounts:profile:confirm-email-request')})

        return self.json_error('accounts.profile.update.form_errors', edit_profile_form.errors)

    @handler('confirm-email', method='get')
    def confirm_email(self, uuid): # pylint: disable=W0621

        task = ChangeCredentialsTaskPrototype.get_by_uuid(uuid)

        context = {'already_processed': False,
                   'timeout': False,
                   'error_occured': False,
                   'task': None}

        if task is None:
            context['wrong_link'] = True
            return self.template('accounts/confirm_email.html', context)

        if task.has_already_processed:
            context['already_processed'] = True
            return self.template('accounts/confirm_email.html', context)

        postponed_task = task.process(logger)

        if task.state == CHANGE_CREDENTIALS_TASK_STATE.TIMEOUT:
            context['timeout'] = True
            return self.template('accounts/confirm_email.html', context)

        if task.state == CHANGE_CREDENTIALS_TASK_STATE.ERROR:
            context['error_occured'] = True
            return self.template('accounts/confirm_email.html', context)

        return self.redirect(postponed_task.wait_url)

    @login_required
    @handler('update-settings', name='update-settings', method='post')
    def update_settings(self):

        settings_form = forms.SettingsForm(self.request.POST)

        if not settings_form.is_valid():
            return self.json_error('accounts.profile.update_settings.form_errors', settings_form.errors)

        self.account.update_settings(settings_form)

        return self.json_ok(data={'next_url': reverse('accounts:profile:edited')})

    @handler('reset-password', method='get')
    def reset_password_page(self):
        if self.account.is_authenticated():
            return self.redirect('/')

        reset_password_form = forms.ResetPasswordForm()
        return self.template('accounts/reset_password.html',
                             {'reset_password_form': reset_password_form} )

    @handler('reset-password-done', method='get')
    def reset_password_done(self):
        if self.account.is_authenticated():
            return self.redirect('/')

        return self.template('accounts/reset_password_done.html', {} )

    @validate_argument('task', ResetPasswordTaskPrototype.get_by_uuid,
                       'accounts.profile.reset_password_done', u'Не получилось сбросить пароль, возможно вы используете неверную ссылку')
    @handler('reset-password-processed', method='get')
    def reset_password_processed(self, task):
        if self.account.is_authenticated():
            return self.redirect('/')

        if task.is_time_expired:
            return self.auto_error('accounts.profile.reset_password_processed.time_expired', u'Срок действия ссылки закончился, попробуйте восстановить пароль ещё раз')

        if task.is_processed:
            return self.auto_error('accounts.profile.reset_password_processed.already_processed',
                                   u'Эта ссылка уже была использована для восстановления пароля, одну ссылку можно использовать только один раз')

        password = task.process(logger=logger)

        return self.template('accounts/reset_password_processed.html', {'password': password} )

    @handler('reset-password', method='post')
    def reset_password(self):

        if self.account.is_authenticated():
            return self.json_error('accounts.profile.reset_password.already_logined', u'Вы уже вошли на сайт и можете просто изменить пароль')

        reset_password_form = forms.ResetPasswordForm(self.request.POST)

        if not reset_password_form.is_valid():
            return self.json_error('accounts.profile.reset_password.form_errors', reset_password_form.errors)

        account = AccountPrototype.get_by_email(reset_password_form.c.email)

        if account is None:
            return self.auto_error('accounts.profile.reset_password.wrong_email', u'На указаный email аккаунт не зарегистрирован')

        ResetPasswordTaskPrototype.create(account)

        return self.json_ok()

    @login_required
    @handler('update-last-news-reminder-time', method='post')
    def update_last_news_reminder_time(self):
        self.account.update_last_news_remind_time()
        return self.json_ok()


class AccountResource(Resource):

    @validate_argument('account', AccountPrototype.get_by_id, 'accounts.account', u'Аккаунт не найден')
    def initialize(self, account=None, *args, **kwargs):
        super(AccountResource, self).initialize(*args, **kwargs)
        self.master_account = account
        self.can_moderate_accounts = self.account.has_perm('accounts.moderate_account')

    @validator(code='accounts.account.moderator_rights_required', message=u'Вы не являетесь модератором')
    def validate_moderator_rights(self, *args, **kwargs): return self.can_moderate_accounts

    @handler('', method='get')
    def index(self, page=1, prefix=''):

        accounts_query = AccountPrototype._model_class.objects.filter(is_fast=False)

        if prefix:
            accounts_query = accounts_query.filter(nick__istartswith=prefix)

        accounts_count = accounts_query.count()

        url_builder = UrlBuilder(reverse('accounts:'), arguments={'page': page,
                                                                  'prefix': prefix})

        page = int(page) - 1

        paginator = Paginator(page, accounts_count, accounts_settings.ACCOUNTS_ON_PAGE, url_builder)

        if paginator.wrong_page_number:
            return self.redirect(paginator.last_page_url, permanent=False)

        account_from, account_to = paginator.page_borders(page)

        accounts_models = accounts_query.select_related().order_by('nick')[account_from:account_to]

        accounts = [AccountPrototype(model) for model in accounts_models]

        accounts_ids = [ model.id for model in accounts_models]

        heroes = dict( (model.account_id, HeroPrototype(model=model)) for model in Hero.objects.filter(account_id__in=accounts_ids))

        return self.template('accounts/index.html',
                             {'heroes': heroes,
                              'prefix': prefix,
                              'accounts': accounts,
                              'current_page_number': page,
                              'paginator': paginator  } )

    @handler('referral-dialog')
    def referral_dialog(self):
        return self.template('accounts/referral_dialog.html',
                             {'account': self.account,
                              'accounts_settings': accounts_settings,
                              'referral_link': full_url('http', 'portal:', **{accounts_settings.REFERRAL_URL_ARGUMENT: self.account.id if self.account else None})} )


    @handler('#account', name='show', method='get')
    def show(self): # pylint: disable=R0914
        from forum.models import Thread
        from game.bills.models import Bill
        from game.bills.relations import BILL_STATE
        from game.ratings.prototypes import RatingPlacesPrototype, RatingValuesPrototype
        from game.phrase_candidates.models import PhraseCandidate

        bills_count = Bill.objects.filter(owner=self.master_account._model).exclude(state=BILL_STATE.REMOVED).count()

        threads_count = Thread.objects.filter(author=self.master_account._model).count()

        threads_with_posts = Thread.objects.filter(post__author=self.master_account._model).distinct().count()

        rating_places = RatingPlacesPrototype.get_by_account_id(self.master_account.id)

        rating_values = RatingValuesPrototype.get_by_account_id(self.master_account.id)

        phrases_count = PhraseCandidate.objects.filter(author=self.master_account._model).count()

        folclor_posts_count = BlogPost.objects.filter(author=self.master_account._model, state=BLOG_POST_STATE.ACCEPTED).count()

        friendship = FriendshipPrototype.get_for_bidirectional(self.account, self.master_account)

        master_hero = HeroPrototype.get_by_account_id(self.master_account.id)

        return self.template('accounts/show.html',
                             {'master_hero': master_hero,
                              'most_common_places': master_hero.places_history.get_most_common_places(),
                              'master_account': self.master_account,
                              'accounts_settings': accounts_settings,
                              'informer_link': accounts_settings.INFORMER_LINK % {'account_id': self.master_account.id},
                              'rating_places': rating_places,
                              'rating_values': rating_values,
                              'bills_count': bills_count,
                              'threads_with_posts': threads_with_posts,
                              'threads_count': threads_count,
                              'folclor_posts_count': folclor_posts_count,
                              'phrases_count': phrases_count,
                              'friendship': friendship} )

    @login_required
    @validate_moderator_rights()
    @handler('#account', 'admin', name='admin', method='get')
    def admin(self):
        from accounts.payments.forms import GMForm
        return self.template('accounts/admin.html',
                             {'master_account': self.master_account,
                              'give_award_form': forms.GiveAwardForm(),
                              'give_money_form': GMForm(),
                              'ban_form': forms.BanForm()} )


    @validate_moderator_rights()
    @handler('#account', 'give-award', name='give-award', method='post')
    def give_award(self):

        form = forms.GiveAwardForm(self.request.POST)

        if not form.is_valid():
            return self.json_error('accounts.account.give_award.form_errors', form.errors)

        AwardPrototype.create(description=form.c.description,
                              type=form.c.type,
                              account=self.master_account)

        return self.json_ok()



    @validate_moderator_rights()
    @handler('#account', 'reset-nick', name='reset-nick', method='post')
    def reset_nick(self):
        task = ChangeCredentialsTaskPrototype.create(account=self.master_account,
                                                     new_nick=u'имя игрока сброшено (%s)' % uuid.uuid4().hex)

        postponed_task = task.process(logger)

        return self.json_processing(postponed_task.status_url)

    @validate_moderator_rights()
    @handler('#account', 'ban', name='ban', method='post')
    def ban(self):

        form = forms.BanForm(self.request.POST)

        if not form.is_valid():
            return self.json_error('accounts.account.ban.form_errors', form.errors)

        if form.c.ban_type._is_FORUM:
            self.master_account.ban_forum(form.c.ban_time.days)
            message = u'Вы лишены права общаться на форуме. Причина: \n\n%(message)s'
        elif form.c.ban_type._is_GAME:
            self.master_account.ban_game(form.c.ban_time.days)
            message = u'Ваш герой лишён возможности влиять на мир игры. Причина: \n\n%(message)s'
        elif form.c.ban_type._is_TOTAL:
            self.master_account.ban_forum(form.c.ban_time.days)
            self.master_account.ban_game(form.c.ban_time.days)
            message = u'Вы лишены права общаться на форуме, ваш герой лишён возможности влиять на мир игры. Причина: \n\n%(message)s'
        else:
            return self.json_error('accounts.account.ban.unknown_ban_type', u'Неизвестный тип бана')

        MessagePrototype.create(get_system_user(),
                                self.master_account,
                                message % {'message': form.c.description})

        return self.json_ok()

    @validate_moderator_rights()
    @handler('#account', 'reset-bans', method='post')
    def reset_bans(self):

        self.master_account.ban_forum(0)
        self.master_account.ban_game(0)

        MessagePrototype.create(get_system_user(),
                                self.master_account,
                                u'С вас сняли все ограничения, наложенные ранее.')

        return self.json_ok()
