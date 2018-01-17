# coding=utf-8
import shutil
import time
import traceback
import os
import zipfile

from idna import unicode
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.permissions import IsAuthenticated

from apps.data.models import RawData
from apps.data.util.remote_operation import Linux
from apps.data.serializers import RawDataSerializer

from apps.data.util.csv_handler import Parser
from apps.data.util.file_walker import FileWalker
from django.http import HttpResponse, HttpResponseNotFound
import mimetypes
import CloudServer.global_settings as global_settings


class DataView(APIView):
    # use session
    authentication_classes = (SessionAuthentication, TokenAuthentication)
    # use permission, in this case, we use the permission subclass from framework
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        """
        处理上传的文件，情况包括：url， 单个文件、一个zip压缩文件
        :param request:
        :return:
        """
        # 文件类型(url, single, zip)
        file_type = request.POST.get('file_type')
        try:
            if file_type == 'single':
                self.upload_and_save(request, need_unzip=False)
            elif file_type == 'zip':
                self.upload_and_save(request, need_unzip=True)
            elif file_type == 'url':
                self.handle_url(request)

        except Exception as e:
            print(traceback.print_exc())
            return Response(status=status.HTTP_400_BAD_REQUEST)
        return Response(status=status.HTTP_200_OK)

    def get(self, request, format=None):
        """
        获得用户所有的数据简介
        :param request:
        :return:
        """
        data = RawData.objects.filter(owner=self.request.user.id)
        serializer = RawDataSerializer(data, many=True)
        return Response(serializer.data)

    def format_name(self, ori_name):
        """
        根据时间生成文件名，防止用户多次上传的文件名一样
        在后缀前加上时间
        TODO 还没考虑后缀名为.tar.gz这种多个组成的情况
        :param ori_name:
        :return:
        """
        name_parts = ori_name.split('.')
        if len(name_parts) == 1:
            filename = name_parts[0] + '_' + time.strftime('%Y%m%d%H%M%S',
                                                           time.localtime(time.time()))
        elif len(name_parts) == 2:
            filename = name_parts[0] + '_' + time.strftime('%Y%m%d%H%M%S',
                                                           time.localtime(time.time())) + '.' + name_parts[1]
        else:
            filename = '.'.join(name_parts[:-1]) + '_' + time.strftime('%Y%m%d%H%M%S',
                                                                       time.localtime(time.time())) + '.' + name_parts[
                           -1]

        return filename

    def upload_and_save(self, request, need_unzip):
        """
        上传文件并保存到数据库
        :param request:
        :return:
        """
        # 文件类别(doc, code, audio, picture)
        file_class = request.POST.get('file_class')
        userid = str(self.request.user.id)
        file = request.FILES.get('file')
        # 根据时间随机生成文件名，防止用户多次上传的文件名一样
        filename = self.format_name(file.name)
        dir_path = 'NJUCloud/' + userid + '/data/' + file_class + '/'
        file_path = dir_path + filename
        self.save_to_local(file=file, dir_path=dir_path, file_path=file_path, need_unzip=need_unzip)
        self.upload_file(file=file, dir_path=dir_path, file_path=file_path, need_unzip=need_unzip)
        self.save_to_db(file_type=file_class, file_path=file_path)

    def save_to_db(self, file_path, file_type):
        """
        存储到数据库
        :param user_id:
        :param file_path:
        :param file_type: RawData.DOC, RawData.CODE, ...
        :return:
        """
        raw_data = RawData(file_path=file_path, file_type=file_type, owner=self.request.user)
        raw_data.save()

    def upload_file(self, file, dir_path, file_path, need_unzip):
        """
        上传文件，如果需要解压则最后need_unzip会是true
        :param file:
        :param dir_path:
        :param file_path:
        :param need_unzip:
        :return:
        """
        host = Linux()
        host.connect()
        host.sftp_upload_file(file, dir_path, file_path,need_unzip)
        host.close()

    def save_to_local(self, file, dir_path, file_path, need_unzip):
        '''
        将文件保存到本地，如果是压缩文件就进行解压缩
        :param file: 文件
        :param dir_path: 文件夹目录 NJUCloud/id/data/type/
        :param file_path: 文件目录 NJUCloud/id/data/type/xxx
        :param need_unzip: 是否需要解压缩
        :return:
        '''
        try:
            dir_path=global_settings.LOCAL_STORAGE_PATH +dir_path
            file_path=global_settings.LOCAL_STORAGE_PATH+file_path
            if not os.path.exists(dir_path):
                os.makedirs(dir_path)
            with open(file_path, 'wb+') as destination:
                for chunk in file.chunks():
                    destination.write(chunk)
            if need_unzip:
                f = zipfile.ZipFile(file_path, 'r')
                for fname in f.namelist():
                    #解决文件名中文乱码问题
                    filename = fname.encode('cp437').decode('utf8')
                    output_filename = os.path.join(dir_path, filename)
                    output_file_dir = os.path.dirname(output_filename)
                    if(filename.endswith('/')):
                        if not os.path.exists(output_file_dir):
                            os.makedirs(output_file_dir)
                    else:
                        with open(output_filename, 'wb') as output_file:
                            shutil.copyfileobj(f.open(fname), output_file)
                f.close()
        except Exception as e:
            print(traceback.print_exc())
            return Response(status=status.HTTP_400_BAD_REQUEST)

    def handle_url(self, request):
        """
        通过url下载数据集到指定位置
        如果url有多个用;隔离开
        :param request:
        :return:
        """
        # 文件类别(doc, code, audio, picture)
        file_class = request.POST.get('file_class')
        userid = str(self.request.user.id)
        urls = request.POST.get('url').split(';')
        filename = self.format_name("url")
        dir_path = 'NJUCloud/' + userid + '/data/' + file_class + '/'
        file_path = dir_path + filename
        host = Linux()
        host.connect()
        for url in urls:
            command = 'wget -c -P ./' + file_path + ' ' + url
            host.send(cmd=command)
        host.close()
        self.save_to_db(file_type=file_class, file_path=file_path)


class DataDetail(APIView):
    def get(self, request, pk, format=None):
        relative_path=request.GET.get('relative_path')
        return self.get_object(pk=pk, relative_path=relative_path)

    def get_object(self,pk, relative_path=None):
        """
        获得一份数据全部内容
        :param relative_path: 目录中文件的相对路径
        :param pk: 数据文件id
        :return:
        """
        raw_data = RawData.objects.get(pk=pk)
        if relative_path is not None:
            userid = str(self.request.user.id)
            # 文件类别(doc, code, audio, picture)
            file_class = raw_data.file_type
            relative_path = 'NJUCloud/' + userid + '/data/' + file_class + '/' +relative_path
            local_file_path = global_settings.LOCAL_STORAGE_PATH + relative_path
            if local_file_path.endswith('.csv'):
                file_type = RawData.DOC
            else:
                file_type = None
        else:
            remote_file_path = raw_data.file_path
            print(remote_file_path)
            # filename = remote_file_path.split('/')[-1]
            # TODO 本地文件存储路径
            local_file_path = global_settings.LOCAL_STORAGE_PATH + remote_file_path
            file_type = raw_data.file_type

            # 先判断在本地是否存在这一份数据的缓存
            if not os.path.exists(local_file_path):
                # 如果不存在，将数据从远程加载到本地
                host = Linux()
                host.connect()
                host.download(remote_path=remote_file_path, local_path=local_file_path)
                host.close()

        # csv 将csv转换为json
        if file_type == RawData.DOC:
            parser = Parser()
            json_data = parser.csv_to_json(local_file_path=local_file_path)
            print(json_data)
            return HttpResponse(json_data, content_type='application/json')

        # 单个图片，或单个音频文件，或单个代码文件，直接返回
        if os.path.isfile(local_file_path):
            try:
                mimetypes.init()
                fsock = open(local_file_path, "rb")
                filename = os.path.basename(local_file_path)
                mime_type_guess = mimetypes.guess_type(filename)
                print(mime_type_guess[0])
                if mime_type_guess is not None:
                    response = HttpResponse(content=fsock.read(), content_type=mime_type_guess[0])
                else:
                    response = HttpResponse(fsock)
                response['Content-Disposition'] = 'attachment; filename=' + filename
            except IOError:
                response = HttpResponseNotFound()
            return response
        else:
            # 以目录形式存储的，返回目录
            walker = FileWalker()
            json_data = walker.get_dir_tree_json(local_file_path)
            return HttpResponse(json_data, content_type='application/json')
