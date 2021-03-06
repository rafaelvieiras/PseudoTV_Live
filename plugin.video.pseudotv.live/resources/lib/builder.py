#   Copyright (C) 2020 Lunatixz
#
#
# This file is part of PseudoTV Live.
#
# PseudoTV Live is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# PseudoTV Live is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with PseudoTV Live.  If not, see <http://www.gnu.org/licenses/>.

# -*- coding: utf-8 -*-

from resources.lib.globals    import *
from resources.lib.parser     import M3U, XMLTV, JSONRPC, Channels
from resources.lib.predefined import Predefined 
from resources.lib.fileaccess import FileLock
from resources.lib.queue      import Worker

class Builder:
    def __init__(self):
        self.log('__init__')
        self.m3u              = M3U()
        self.xmltv            = XMLTV()
        self.channels         = Channels()
        self.predefined       = Predefined()
        self.queue            = Worker()
        self.jsonRPC          = JSONRPC(self.queue)
        self.incStrms         = INCLUDE_STRMS  #todo adv. rules
        self.incExtras        = INCLUDE_EXTRAS #todo adv. rules
        self.maxDays          = MAX_GUIDE_DAYS
        self.fillBCTs         = ENABLE_BCTS
        self.strictDuration   = STRICT_DURATION
        self.saveDuration     = STORE_DURATION
        self.accurateDuration = ACCURATE_DURATION
        self.grouping         = ENABLE_GROUPING
        self.now              = getLocalTime()
        self.start            = roundToHalfHour(self.now)
        self.dialog           = None
        self.progress         = 0
        self.channelCount     = 0
        self.msg              = ''
    
    
    def log(self, msg, level=xbmc.LOGDEBUG):
        return log('%s: %s'%(self.__class__.__name__,msg),level)
    

    # Run rules for a channel
    def runActions(self, action, channelID, parameter):
        self.log("runActions %s on channel %s"%(action,channelID))
        self.runningActionChannel = channelID
        index = 0
        channelList =  sorted(self.createChannelItems(), key=lambda k: k['number'])
        ruleList = [item['ruleList'] for item in channelList if channelID == item['id']]
        for rule in ruleList:
            if rule.actions & action > 0:
                self.runningActionId = index
                parameter = rule.runAction(action, self, parameter)
            index += 1
        self.runningActionChannel = 0
        self.runningActionId = 0
        return parameter


    def createChannelItems(self):
        self.log('createChannelItems')
        items = sorted(self.channels.getChannels(), key=lambda k: k['number']) # user-defined
        items.extend(sorted(self.predefined.buildChannelList(), key=lambda k: k['number'])) # pre-defined
        for idx, item in enumerate(items):
            if not item.get('path',None) or not item.get('name',None): 
                self.log('createChannelItems; skipping, missing channel path and/or channel name\n%s'%(dumpJSON(item)))
                continue
                
            item['label']  = item['name'] #todo drop 'label'?
            item['id']     = (item.get('id','')    or getChannelID(item['name'], item['path'])) # internal use only; use provided id for future xmltv pairing, else create unique Pseudo ID.
            item['logo']   = (item.get('logo','')  or self.jsonRPC.getLogo(item['name'], item['type'], item['path'], featured=True))
            item['radio']  = (item.get('radio','') or item['path'][0].startswith('musicdb://'))
            item['number'] = item.get('number','') or random.sample(CHANNEL_RANGE,1)[0] # use static channel number or produce dynamic value for pre-defined channels.
            item['url']    = 'plugin://%s/?mode=play&name=%s&id=%s&radio=%s'%(ADDON_ID,urllib.parse.quote(item['name']),urllib.parse.quote(item['id']),str(item['radio']))
            item['group']  = [ADDON_NAME]
            if self.grouping: item['group'].extend(item.get('groups',[]))
            yield item


    def reload(self):
        self.log('reload')
        try:
            self.channels.reset()
            self.xmltv.reset()
            self.m3u.reset()
            return True
        except Exception as e: self.log("reload, Failed! " + str(e), xbmc.LOGERROR)
        return False
        
        
    def save(self):
        self.log('save')
        try:
            self.m3u.save()
            self.xmltv.save()
            return True
        except Exception as e: self.log("save, Failed! " + str(e), xbmc.LOGERROR)
        return False
        
        
    def buildService(self, channels=None, reloadPVR=True, update=False):
        self.log('buildService, update = %s'%(update))
        if isBusy(): 
            return notificationDialog(LANGUAGE(30029)%(ADDON_NAME))
            
        if channels is None: 
            channels = sorted(self.createChannelItems(), key=lambda k: k['number'])
        if not channels: 
            return notificationDialog(LANGUAGE(30056))
            
        setBusy(True)
        if not self.reload(): 
            return notificationDialog(LANGUAGE(30001))
                    
        # if self.saveDuration: 
            # self.queue.run() #todo debug kodi hanging on exit, threads not shutting down
        self.fileLock = FileLock()
        self.fileLock.lockFile("MasterLock")
        msg = LANGUAGE(30051) if update else LANGUAGE(30050)
        self.dialog = ProgressBGDialog(message=LANGUAGE(30052))
        self.channelCount = len(channels)
        for idx, channel in enumerate(channels):
            self.progress = (idx*100//len(channels))
            cacheResponse = self.getFileList(channel, channel['radio']) # {True:'Valid Channel exceed MAX_DAYS',False:'In-Valid Channel',list:'fileList'}
            if not cacheResponse: 
                continue
            
            self.msg    = '%s, %s'%(msg,channel['name'])
            self.buildM3U(channel, channel['radio'])
            self.dialog = ProgressBGDialog(self.progress, self.dialog, message=self.msg)
            self.buildM3U(channel, channel['radio'])
            if isinstance(cacheResponse,list):
                self.buildXMLTV(channel, cacheResponse, channel['radio'])
                
        self.save()
        setBusy(False)
        self.fileLock.unlockFile('MasterLock')
        self.fileLock.close()
        self.dialog = ProgressBGDialog(100, self.dialog, message=LANGUAGE(30053))
        if reloadPVR: checkPVR()


    def getFileList(self, channel, radio=False):
        self.log('getFileList; channel = %s, radio = %s'%(channel,radio))
        try:
            self.now   = getLocalTime()
            self.start = (self.xmltv.xmltvList['endtimes'].get(channel['id'],'') or roundToHalfHour(self.now)) #offset time to start on half hour

            if datetime.datetime.fromtimestamp(self.start) >= (datetime.datetime.fromtimestamp(self.now) + datetime.timedelta(days=self.maxDays)): 
                self.log('getFileList, id: %s programmes exceed MAX_DAYS: endtime = %s'%(channel['id'],self.start),xbmc.LOGINFO)
                return True# prevent over-building

            # global values prior to channel rules
            filter = {}
            sort   = {}#{"order": "ascending", "ignorefolders": "false", "method": "random"}
            limits = {}#adv. rule to force page.
            # todo load pre json rules.
            
            if isinstance(channel['path'], list): 
                mixed = True # build 'mixed' channels ie more than one path.
                path  = channel['path']
            else:
                mixed = False
                path  = [channel['path']] 
            limit = int(PAGE_LIMIT//len(path))# equally distribute content between multi-paths. 
            
            if radio:
                media = 'music'
            else: 
                media = 'video'

            if radio:
                cacheResponse = self.buildRadioList(channel)
            else:         
                cacheResponse = [self.buildFileList(channel, file, media, limit, sort, filter, limits) for file in path] # build multi-paths as induvial arrays for easier interleaving.
                if not cacheResponse: 
                    self.log("getFileList, id: %s skipping channel cacheResponse empty!"%(channel['id']),xbmc.LOGINFO)
                    return False
                
                cacheResponse = list(interleave(*cacheResponse)) # interleave multi-paths, while keeping order.
                # todo load post json rules.
            
            cacheResponse = self.addScheduling(channel, cacheResponse)
            if self.fillBCTs: 
                cacheResponse = self.injectBCTs(channel, cacheResponse)
            # todo load finale json rules.
            return sorted((cacheResponse), key=lambda k: k['start'])
        except Exception as e: self.log("getFileList, Failed! " + str(e), xbmc.LOGERROR)
        return False
            
        
    def getDuration(self, path, item={}, accurate=False):
        self.log("getDuration; accurate = %s, path = %s"%(accurate,path))
        duration = 0
        runtime  = int(item.get('runtime','') or item.get('duration','0') or '0')
        if accurate == False or path.startswith(('plugin://','upnp://','pvr://')): return runtime
        elif path.startswith('stack://'): #handle "stacked" videos:
            stack = (path.replace('stack://','').replace(',,',',')).split(' , ') #todo move to regex match
            for file in stack: duration += self.jsonRPC.parseDuration(file, item)
        else: 
            duration = self.jsonRPC.parseDuration(path, item)
        if self.strictDuration or duration > 0: 
            return duration
        return runtime 
        
    
    def injectBCTs(self, channel, fileList):
        self.log("injectBCTs; channel = %s"%(channel))
        if channel['radio'] == True: 
            return fileList
            
        tmpList       = []
        bctTypes      = ['rating'] #todo check rules for type and pack option
        bctPacks      = None #todo check adv. rules for pack details
        resourceMap   = self.jsonRPC.buildBCTresources(bctPacks)
        
        
        def buildPath(path, file):
            return path.replace('resource://','special://home/addons/') + '/resources/%s'%(file)
        
        
        def fillSingle(item, matchType, resource, paths, files):
            for file in files:
                filename, ext = os.path.splitext(file)
                if matchType.lower() == filename.lower():
                    BCTfilepath = buildPath(resource['path'], file)
                    BCTduration = self.jsonRPC.parseDuration(BCTfilepath)
                    self.log("injectBCTs; fillSingle matching = %s, BCTfilepath = %s, BCTduration = %s"%(matchType,BCTfilepath,BCTduration))
                    paths.insert(0,BCTfilepath) # don't add BCTduration to PRE_ROLL.
                    # item['stop'] += BCTduration  
                    break
            
            
        def fillRandom(item, matchType, resource, paths, dirs, count=None, time=None):
            sfiles = []
            rmap   = [{'path':os.path.join(resource['path'],dir), 'files':self.jsonRPC.getResourcesFolders(os.path.join(resource['path'],dir))[1]} for dir in dirs if matchType.lower() == dir.lower()]
            if rmap:
                files = rmap.get('files',[])
                random.shuffle(files)
                if count is not None:
                    sfiles = random.sample(files, count)
                    for file in sfiles:
                        BCTfilepath = buildPath(rmap['path'], file)
                        BCTduration = self.jsonRPC.parseDuration(file)
                        self.log("injectBCTs; fillRandom matching = %s, BCTfilepath = %s, BCTduration = %s"%(matchType,BCTfilepath,BCTduration))
                        paths.append(BCTfilepath)
                        item['stop'] += BCTduration                       
                # else:
                    #
                
                                             
        for item in fileList:
            # type      = item.get('type'  ,'')
            # studios   = item.get('studio',[])
            # start     = item['start']
            stop      = item['stop']
            endOnHour = (roundToHour(stop) - stop)
            paths     = [item['file']]
            orgPaths  = paths.copy()
            stack     =  'stack://%s'

            for bctType in bctTypes:
                resource = resourceMap.get(bctType,{})
                dirs     = resource.get('dirs',[])
                files    = resource.get('files',[])
                
                if bctType == 'rating':
                    mpaa = item.get('mpaa'  ,'')
                    if mpaa.startswith('Rated'): mpaa = re.split('Rated ',mpaa)[1]  #todo prop. regex
                    fillSingle(item, mpaa, resource, paths, files)
                else:
                    fillRandom(item, channel['name'], resource, paths, dirs)
                                    
            if orgPaths != paths:
                item['originalfile'] = item['file']
                item['file'] = stack%(' , '.join(paths))
            tmpList.append(item)
        return tmpList

        
    def addScheduling(self, channel, fileList):
        self.log("addScheduling; channel = %s"%(channel))
        #todo insert adv. scheduling rules here or move to adv. rules.py
        tmpList = []
        for idx, item in enumerate(fileList):
            item["idx"]   = idx
            item['start'] = self.start
            item['stop']  = self.start + item['duration']
            self.start    = item['stop']
            tmpList.append(item)
        return tmpList
            

    def buildRadioList(self, channel):
        self.log("buildRadioList; channel = %s"%(channel))
        #todo insert custom radio labels,plots based on genre type?
        channel['genre'] = [channel['name']]
        channel['art']   = {'thumb':channel['logo'],'icon':channel['logo'],'fanart':channel['logo']}
        channel['plot']  = LANGUAGE(30098)%(channel['name'])
        return self.buildSingleCell(channel,type='music')
                
                
    def buildSingleCell(self, channel, duration=10800, type='video', entries=3):
        self.log("buildSingleCell; channel = %s"%(channel))
        tmpItem  = {'label'       : (channel.get('label','') or channel['name']),
                    'episodetitle': channel.get('episodetitle',''),
                    'plot'        : (channel.get('plot' ,'') or xbmc.getLocalizedString(161)),
                    'genre'       : channel.get('genre',['Undefined']),
                    'type'        : type,
                    'duration'    : duration,
                    'start'       : 0,
                    'stop'        : 0,
                    'art'         : channel.get('art',{})}
        return [tmpItem.copy() for idx in range(entries)]
        
        
    def buildXMLTV(self, channelData, fileList, radio=False):
        self.log("buildXMLTV, channel = %s"%(channelData))
        self.xmltv.addChannel(channelData)
        for idx, file in enumerate(fileList):
            if not file: continue
           
            item = {}
            item['channel']     = channelData['id']
            item['start']       = file['start']
            item['stop']        = file['stop']
            item['title']       = file['label']
            item['desc']        = file['plot']
            item['sub-title']   = file.get('episodetitle','')
            item['rating']      = (file.get('mpaa','')   or 'NA')
            item['stars']       = (file.get('rating','') or '0')
            item['categories']  = (file.get('genre','')  or ['Undefined'])
            item['type']        = file.get('type','video')
            item['new']         = int(file.get('playcount',0)) == 0
            item['thumb']       = getThumb(file)
            item['date']        = (file.get('firstaired','') or file.get('premiered','') or file.get('releasedate','') or file.get('originaldate','') or None)
            
            item['episode-num'] = ''
            if (item['type'] != 'movie' and (file.get("episode",0) > 0)):
                item['episode-num'] = 'S%sE%s'%(str(file.get("season",0)).zfill(2),str(file.get("episode",0)).zfill(2))
                
            # key hijacking
            item['director']    = str(file.get('id',''))# dbid
            file['data']        = channelData #channel dict
            item['writer']      = file # kodi listitem dict.
            self.xmltv.addProgram(channelData['id'], item)
            
            
    def buildM3U(self, channel, radio=False):
        self.log("buildM3U, channel = %s"%(channel))
        self.m3u.add(channel, radio)


    def buildFileList(self, channel, path, media='video', limit=PAGE_LIMIT, sort={}, filter={}, limits={}):
        self.log("buildFileList, path = %s, limit = %s, sort = %s, filter = %s, limits = %s"%(path,limit,sort,filter,limits))
        id            = channel['id']
        fileList      = []
        seasoneplist  = []
        method        =  sort.get("method",'random')
        json_response = self.jsonRPC.requestList(id, path, media, limit, sort, filter, limits)
        for item in json_response:
            file = item.get('file','')
            fileType = item.get('filetype','file')

            if fileType == 'file':
                if file[-4].lower() == 'strm' and not self.incStrms: 
                    self.log("buildFileList, id: %s skipping strm!"%(id),xbmc.LOGINFO)
                    continue
                    
                dur = self.getDuration(file, item, self.accurateDuration)
                if dur > 0:
                    item['duration'] = dur
                    if int(item.get("year","0")) == 1601: 
                        item['year'] = 0 #default null for kodi rpc?                     
                    mType   = item['type']
                    label   = item['label']
                    title   = (item.get("title",label) or label)
                    tvtitle = item.get("showtitle","")
                    
                    if tvtitle:
                        # This is a TV show
                        # method  = 'episode' #todo move to rules, ie sort parameter
                        seasonval = int(item.get("season","0"))
                        epval     = int(item.get("episode","0"))
                        if not self.incExtras and (seasonval == 0 or epval == 0): 
                            self.log("buildFileList, id: %s skipping extras!"%(id),xbmc.LOGINFO)
                            continue
                            
                        if epval > 0: title = title + ' (' + str(seasonval) + 'x' + str(epval).zfill(2) + ')'
                        item["episodetitle"] = title
                        label = tvtitle
                        item['tvshowtitle'] = tvtitle
                        
                    else: # This is a Movie
                        years = int(item.get("year","0"))
                        if years > 0: title = "%s (%s)"%(title, years)
                        item["episodetitle"] = item.get("tagline","")
                        seasonval = None
                        label = title
            
                    if self.dialog is not None and self.msg.startswith(LANGUAGE(30051)):
                        self.dialog = ProgressBGDialog(self.progress, self.dialog, message='%s: %s'%(self.msg,((len(fileList)*100)//PAGE_LIMIT))+'%')
                    
                    item['label']  = label
                    item['plot']   = (item.get("plot","") or item.get("plotoutline","") or item.get("description","") or xbmc.getLocalizedString(161))
                 
                    if method == 'episode' and seasonval is not None: 
                        seasoneplist.append([seasonval, epval, item])
                    else: 
                        fileList.append(item)
            
                else: 
                    self.log("buildFileList, id: %s skipping no duration meta found!"%(id),xbmc.LOGINFO)
                    
            elif fileType == 'directory' and (len(fileList) < limit): #extend fileList by parsing folders, limit folder parsing to limit size to avoid runaways.
                fileList.extend(self.buildFileList(channel, file, media, limit, sort, filter, limits))
            
        if method == 'episode':
            seasoneplist.sort(key=lambda seep: seep[1])
            seasoneplist.sort(key=lambda seep: seep[0])
            for seepitem in seasoneplist: 
                fileList.append(seepitem[2])
            
        self.log("buildFileList, id: %s returning fileList %s / %s"%(id,len(fileList),limit),xbmc.LOGINFO)
        return fileList