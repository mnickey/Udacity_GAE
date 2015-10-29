#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'


from datetime import datetime
import json, os, time, endpoints
from protorpc import messages, message_types, remote

from google.appengine.api import urlfetch, memcache, taskqueue
from google.appengine.ext import ndb

from models import Profile, ProfileMiniForm, ProfileForm, TeeShirtSize, Conference, ConferenceForm
from models import ConferenceForms, ConferenceQueryForm, ConferenceQueryForms, BooleanMessage
from models import ConflictException, StringMessage, Session, SessionForm, SessionForms

from settings import WEB_CLIENT_ID
from utils import getUserId

DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": ["Default", "Topic"],
}

OPERATORS = {
            'EQ':   '=',
            'GT':   '>',
            'GTEQ': '>=',
            'LT':   '<',
            'LTEQ': '<=',
            'NE':   '!='
            }

FIELDS = {
    'CITY': 'city',
    'TOPIC': 'topics',
    'MONTH': 'month',
    'MAX_ATTENDEES': 'maxAttendees',
}

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

SESSION_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

SESSION_POST_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

SESSION_TYPE = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
    typeOfSession=messages.StringField(2),
)

SESSION_SPEAKER = endpoints.ResourceContainer(
    message_types.VoidMessage,
    speaker=messages.StringField(1),
)

SESSION_NAME = endpoints.ResourceContainer(
    message_types.VoidMessage,
    name=messages.StringField(1),
)

SESSION_HIGHLIGHTS = endpoints.ResourceContainer(
    message_types.VoidMessage,
    highlights=messages.StringField(1),
)

SESSION_WISHLIST_POST_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

WISHLIST_GET_REQUEST_BY_TYPE = endpoints.ResourceContainer(
    typeOfSession=messages.StringField(1)
)

SESSION_SPEAKER_REQUEST = endpoints.ResourceContainer(
    typeOfSession=messages.StringField(1)
)

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"
ANNOUNCEMENT_TPL = ('Last chance to attend! The following conferences '
                    'are nearly sold out: %s')
SESSION_DEFAULTS = {
    "highlights": "Coming Soon",
    "duration": 60,
}
# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -


@endpoints.api(name='conference',
               version='v1',
               allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID],
               scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""
# - - - Profile objects - - - - - - - - - - - - - - - - - - -
    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf

    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if non-existent."""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        user_id = getUserId(user)  # step 2. get user id by calling getUserId(user)
        profile_key = ndb.Key(Profile, user_id)  # step 3. create a new key of kind Profile from the id
        profile = profile_key.get()

        if not profile:
            profile = Profile(
                key=profile_key,
                displayName=user.nickname(),
                mainEmail=user.email(),
                teeShirtSize=str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()   # save the profile to datastore
        return profile      # return Profile

    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifiable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
            prof.put()      # put the modified profile to data store

        # return ProfileForm
        return self._copyProfileToForm(prof)

    @endpoints.method(message_types.VoidMessage, ProfileForm, path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()

    @endpoints.method(ProfileMiniForm, ProfileForm,
                      path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        # _doProfile(request)
        return self._doProfile(request)

# - - - Conference objects - - - - - - - - - - - - - - - - -
    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
                      path='conference/{websafeConferenceKey}',
                      http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf

    def _createConferenceObject(self, request):
        """Create or update Conference object, returning ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        # both for data model & outbound Message
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
            setattr(request, "seatsAvailable", data["maxAttendees"])

        # make Profile Key from user ID
        p_key = ndb.Key(Profile, user_id)
        # allocate new Conference ID with Profile key as parent
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        # make Conference key from ID
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference & return (modified) ConferenceForm
        Conference(**data).put()
        taskqueue.add(params={'email': user.email(),
                      'conferenceInfo': repr(request)},
                      url='/tasks/send_confirmation_email')

        return request

    @endpoints.method(ConferenceForm, ConferenceForm,
                      path='conference',
                      http_method='POST',
                      name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)

    @endpoints.method(ConferenceQueryForms, ConferenceForms,
                      path='queryConferences',
                      http_method='POST',
                      name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)
        # return individual ConferenceForm object per Conference
        return ConferenceForms(items=[self._copyConferenceToForm(conf, "") for conf in conferences])

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
                      path='getConferencesCreated',
                      http_method='POST',
                      name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        # make profile key
        p_key = ndb.Key(Profile, getUserId(user))
        # create ancestor query for this user
        conferences = Conference.query(ancestor=p_key)
        # get the user profile and display name
        prof = p_key.get()
        displayName = getattr(prof, 'displayName')
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, displayName) for conf in conferences])

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
                      path='filterPlayground',
                      http_method='GET',
                      name='filterPlayground')
    def filterPlayground(self, request):
        """Filter Playground"""
        q = Conference.query()
        # TODO
        # add 2 filters:
        # 1: city equals to London
        # 2: topic equals "Medical Innovations"
        q = q.filter(Conference.city == "London")
        q = q.filter(Conference.topics == "Medical Innovations")
        q = q.order(Conference.name)
        q = q.filter(Conference.seatsAvailable > 10)
        return ConferenceForms(items=[self._copyConferenceToForm(conf, "") for conf in q])

    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q

    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)

# - - - Registration - - - - - - - - - - - - - - - - - - - -

    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser()  # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)

    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
                      path='conference/{websafeConferenceKey}',
                      http_method='POST',
                      name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
                      path='conferences/attending',
                      http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        # TODO:
        # step 1: get user profile
        prof = self._getProfileFromUser()
        # step 2: get conferenceKeysToAttend from profile.
        conf_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.conferenceKeysToAttend]
        # to make a ndb key from websafe key you can use:
        # ndb.Key(urlsafe=my_websafe_key_string)
        # step 3: fetch conferences from data store.
        # Use get_multi(array_of_keys) to fetch all keys at once.
        # Do not fetch them one by one!
        conferences = ndb.get_multi(conf_keys)

        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(items=[self._copyConferenceToForm(conf,
                                                                 names[conf.organizerUserId]) for conf in conferences])


# - - - Session objects - - - - - - - - - - - - - - - - -

    def _createSessionObject(self, request):
        """ creates & updates session object, returning the SessionForm request """
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException("Authorization required")
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Session 'name' field required.")
        # copy sessionForm info into a dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        for defaultValue in SESSION_DEFAULTS:
            if data[defaultValue] in (None, []):
                data[defaultValue] = SESSION_DEFAULTS[defaultValue]
                setattr(request, defaultValue, SESSION_DEFAULTS[defaultValue])
        # Convert dates and times to date objects
        if data['startTime']:
            data['startTime'] = datetime.strptime(data['startTime'], "%H:%M").time()
        if data['date']:
            data['date'] = datetime.strptime(data['date'][:10], "%Y-%m-%d").date()
        # remove unused items
        del data['websafeKey']
        del data['websafeConferenceKey']

        # Create key based off of conference key
        conf_key = ndb.Key(urlsafe=request.websafeConferenceKey)
        # Create session ID using conf_key as parent
        session_id = Session.allocate_ids(size=1, parent=conf_key)[0]
        # create session key using the session ID and conf_key
        session_key = ndb.Key(Session, session_id, parent=conf_key)

        # Assign the key
        data['key'] = session_key
        # Push the session to the data store
        session = Session(**data)
        session.put()

        return self._copySessionToForm(session)

    def _copySessionToForm(self, session):
        """ Copies the fields from session to sessionForm """
        sessForm = SessionForm()
        for field in session.all_fields():
            if hasattr(sessForm, field.name):
                if field.name.endswith('Date'):
                    setattr(sessForm, field.name, str(getattr(session, field.name)))
                else:
                    setattr(sessForm, field.name, getattr(session, field.name))
            elif field.name == "websafeKey":
                setattr(sessForm, field.name, session.key.urlsafe())
        sessForm.check_initialized()
        return sessForm

    @endpoints.method(SESSION_POST_REQUEST, SessionForm,
                      path='conference/{websafeConferenceKey}/sessions',
                      http_method='POST',
                      name='createSession')
    def createConferenceSession(self, request):
        """ creates a conference session """
        return self._createSessionObject(request)

    @endpoints.method(SESSION_GET_REQUEST, SessionForms,
                      path='conference/{websafeConferenceKey}/sessions',
                      http_method='GET',
                      name='getConferenceSessions')
    def getConferenceSessions(self, request):
        """ Given a conference, return all sessions of a specified type (eg lecture, keynote, workshop) """
        # get the conference key
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        # check the conference key
        if not conf:
            raise endpoints.NotFoundException("No conference found with that key. %s" % request.websafeConferenceKey)
        # query the data store
        sessions = Session.query(ancestor=conf.key)
        return SessionForms(items=[self._copySessionToForm(session) for session in sessions])

    @endpoints.method(SESSION_TYPE, SessionForms,
                      path='conference/type/{websafeConferenceKey}',
                      http_method='GET',
                      name='getConferenceSessionsByType')
    def getConferenceSessionsByType(self, request):
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        sessions = Session.query(ancestor=conf.key).filter(Session.typeOfSession == request.typeOfSession)
        return SessionForms(items=[self._copySessionToForm(session) for session in sessions])

    @endpoints.method(SESSION_SPEAKER, SessionForms,
                      path='sessions/speaker',
                      http_method='POST',
                      name='getConferenceSessionsBySpeaker')
    def getConferenceSessionsBySpeaker(self, request):
        """ Given a speaker, return all sessions given by this particular speaker, across all conferences """
        sessions = Session.query(Session.speaker == request.speaker)
        return SessionForms(items=[self._copySessionToForm(session) for session in sessions])

    @endpoints.method(SESSION_NAME, SessionForm,
                      path='sessions/name',
                      http_method='POST',
                      name='getConferenceSessionsByName')
    def getConferenceSessionsByName(self, request):
        """ Get sessions by name """
        sessions = Session.query(Session.name == request.name)
        return SessionForms(items=[self._copySessionToForm(session) for session in sessions])

    @endpoints.method(SESSION_HIGHLIGHTS, SessionForm,
                      path='sessions/highlights',
                      http_method='POST',
                      name='getConferenceSessionsByHighlights')
    def getConferenceSessionsByHighlights(self, request):
        sessions = Session.query(Session.highlights == request.highlights)
        return SessionForms(items=[self._copySessionToForm(session) for session in sessions])

# - - - Wishlist - - - - - - - - - - - - - - - - - - - -
    @endpoints.method(SESSION_WISHLIST_POST_REQUEST, BooleanMessage,
                      path="addToWishlist",
                      http_method="POST",
                      name="addSessionToWishList")
    def addSessionToWishlist(self, request):
        """Adds the session to the users's list of sessions based on interest."""
        retval = None
        prof = self._getProfileFromUser()
        wsck = request.websafeConferenceKey
        session = ndb.Key(urlsafe=wsck).get()

        if not session:
            raise endpoints.NotFoundException('No Session found with key: %s' % wsck)

        if wsck in prof.wishlistKeys:
            raise ConflictException('You have already added this to your wishlist.')
        else:
            prof.wishlistKeys.append(wsck)
            retval = True

        prof.put()
        session.put()
        return BooleanMessage(data=retval)

    @endpoints.method(message_types.VoidMessage, SessionForms,
                      path='wishlist',
                      http_method='GET',
                      name='getSessionsInWishlist')
    def getSessionsInWishlist(self, request):
        """Query the sessions in a users wishlist"""
        prof = self._getProfileFromUser()
        session_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.wishlistkeys]
        wish_list_sessions = ndb.get_multi(session_keys)
        return SessionForms(
            items=[self._copySessionToForm(x) for x in wish_list_sessions]
        )

# - - - Additional Queries - - - - - - - - - - - - - - - - - - - -
    @endpoints.method(WISHLIST_GET_REQUEST_BY_TYPE, SessionForms,
                      path="withlist/type",
                      http_method="GET",
                      name="getSessionsInWishlistByType")
    def getSessionsInWishlistByType(self, request):
        """Return a wishlist filtered by type for the user"""
        prof = self._getProfileFromUser()
        session_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.wishlistKeys]
        wish_list_sessions = ndb.get_multi(session_keys)
        requested_session_type = Session.query(Session.typeOfSession == request.typeOfSession)
        return SessionForms(items=[self._copySessionToForm(x) for x in wish_list_sessions
                                   if x in requested_session_type]
                            )

    @endpoints.method(SESSION_SPEAKER_REQUEST, SessionForms,
                      path="wishlist/speaker",
                      http_method="GET",
                      name="getSessionInWishlistBySpeaker")
    def getSessionsInWishlistBySpeaker(self, request):
        """Return users wishlist filtered by speaker"""
        prof = self._getProfileFromUser()
        session_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.wishlistKeys]
        wishlist_sessions = ndb.get_multi(session_keys)
        sessions_by_speaker = Session.query(Session.speaker == request.speaker)
        return SessionForms(
            items=[self._copySessionToForm(x) for x in wishlist_sessions
                   if x in sessions_by_speaker]
        )

# - - - Announcements - - - - - - - - - - - - - - - - - - - -
    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = ANNOUNCEMENT_TPL % (', '.join(conf.name for conf in confs))
            # announcement = '%s %s' % (
            #     'Last chance to attend! The following conferences '
            #     'are nearly sold out:',
            #     ', '.join(conf.name for conf in confs))
            print "The announcement has been created. Here: ", announcement
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
            print "The announcement has been set."
        else:
            print "We are going to delete the announcement from memcache"
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            print "no announcement: ", announcement
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)
            print "The announcement has been deleted from memcache."

        return announcement

    @endpoints.method(message_types.VoidMessage, StringMessage,
                      path='conference/announcement/get',
                      http_method='GET',
                      name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        announcement = memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY)
        if not announcement:
            announcement = ""
        return StringMessage(data=announcement)

# registers API
api = endpoints.api_server([ConferenceApi]) 
