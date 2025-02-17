# -------------------------------------------------------------------------
#
# Copyright (c) 2023 General Motors GTO LLC
#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: 2023 General Motors GTO LLC
# SPDX-License-Identifier: Apache-2.0
#
# -------------------------------------------------------------------------


import logging
import time
from datetime import datetime

from google.protobuf import any_pb2
from google.protobuf.json_format import MessageToDict
from uprotocol.proto.upayload_pb2 import UPayload
from uprotocol.rpc.rpcmapper import RpcMapper

import simulator.utils.constant as CONSTANTS
from simulator.utils import common_util
from simulator.core import protobuf_autoloader
from simulator.utils.common_util import flatten_dict
from simulator.ui.utils.file_utils import save_rpc_data, save_pub_sub_data

total_rpc = 0
success_rpc = 0
logger = logging.getLogger("Simulator")


def rpc_response_handler(socketio, message):
    """
    This callback function get invoked when response received for rpc request
    """
    members = MessageToDict(message, preserving_proto_field_name=True, including_default_value_fields=True)
    socketio.emit(CONSTANTS.CALLBACK_SENDRPC_RESPONSE, members, namespace=CONSTANTS.NAMESPACE)


def rpc_logger_handler(socketio, lock_rpc, rpc_request, method_name, json_data, rpcdata):
    global total_rpc, success_rpc
    try:
        rpc_response = MessageToDict(json_data, preserving_proto_field_name=True, including_default_value_fields=True)
        rpc_method_name = method_name
        rpc_request = MessageToDict(rpc_request, preserving_proto_field_name=True, including_default_value_fields=True)

        publishedData = ""
        if len(rpcdata) > 0:
            publishedData = MessageToDict(
                rpcdata[len(rpcdata) - 1], preserving_proto_field_name=True, including_default_value_fields=True
            )
        total_rpc = total_rpc + 1
        isfailed = True
        if (
            rpc_response.__contains__(CONSTANTS.KEY_MESSAGE)
            and rpc_response[CONSTANTS.KEY_MESSAGE].__contains__("OK")
            or rpc_response.__contains__(CONSTANTS.KEY_CODE)
            and rpc_response[CONSTANTS.KEY_CODE] == 0
            or rpc_response.__contains__(CONSTANTS.KEY_STATUS)
            and type(rpc_response[CONSTANTS.KEY_STATUS]) is dict
            and rpc_response[CONSTANTS.KEY_STATUS][CONSTANTS.KEY_MESSAGE].__contains__("OK")
            or rpc_response.__contains__(CONSTANTS.KEY_CODE)
            and type(rpc_response[CONSTANTS.KEY_CODE]) is dict
            and rpc_response[CONSTANTS.KEY_CODE][CONSTANTS.KEY_MESSAGE].__contains__("OK")
        ) or method_name == "SayHello":
            success_rpc = success_rpc + 1
            isfailed = False
        failed_rpc = total_rpc - success_rpc
        now = datetime.now()
        dt_string = now.strftime("%d %b, %Y %H:%M:%S")
        json_res = {
            CONSTANTS.KEY_METHODNAME: rpc_method_name,
            CONSTANTS.KEY_REQUEST: rpc_request,
            CONSTANTS.KEY_RESPONSE: rpc_response,
            CONSTANTS.KEY_DATA: publishedData,
            CONSTANTS.KEY_RPCCOUNT: total_rpc,
            CONSTANTS.KEY_SUCCESSRPC: success_rpc,
            CONSTANTS.KEY_FAILEDRPC: failed_rpc,
            CONSTANTS.KEY_ISFAILED: isfailed,
            CONSTANTS.KEY_TIME: dt_string,
        }
        save_rpc_data(socketio, lock_rpc, json_res)
    except Exception as ex:
        logger.error("Exception handler:", exc_info=ex)


def subscribe_status_handler(socketio, lock_pubsub, utransport, topic, status_code, status_message):
    logger.debug(f"Topic: {topic}, Status Code: {status_code}, Status Message: {status_message}")

    if status_code == 0:
        socketio.oldtopic = topic
        json_res = {"type": "Subscribe", "topic": topic, "status": "Success"}
        save_pub_sub_data(socketio, lock_pubsub, json_res)
        message = "Successfully subscribed to " + topic

        if utransport == "ZENOH":
            message = "Successfully subscribed to  " + topic + " to ZENOH"
        socketio.emit(CONSTANTS.CALLBACK_SUBSCRIBE_STATUS_SUCCESS, message, namespace=CONSTANTS.NAMESPACE)
    else:
        json_res = {"type": "Subscribe", "topic": topic, "status": "Failed"}
        save_pub_sub_data(socketio, lock_pubsub, json_res)
        socketio.emit(
            CONSTANTS.CALLBACK_SUBSCRIBE_STATUS_FAILED,
            f"Unsuccessful subscription for {topic} as the status code is {status_code} with "
            f"status message {status_message}",
            namespace=CONSTANTS.NAMESPACE,
        )


def publish_status_handler(socketio, lock_pubsub, utransport, topic, status_code, status_message, last_published_data):
    if status_code == 0:
        json_res = {
            "type": "Publish",
            "topic": topic,
            "transport": utransport,
            "status": "Success",
            "message": last_published_data,
        }
        save_pub_sub_data(socketio, lock_pubsub, json_res)
        message = "Successfully published message for " + topic
        if utransport == "ZENOH":
            message = "Successfully published message for " + topic + " to ZENOH"
        socketio.emit(CONSTANTS.CALLBACK_PUBLISH_STATUS_SUCCESS, {"msg": message}, namespace=CONSTANTS.NAMESPACE)

    else:
        json_res = {
            "type": "Publish",
            "topic": topic,
            "transport": utransport,
            "status": "Failed",
            "message": last_published_data,
        }
        save_pub_sub_data(socketio, lock_pubsub, json_res)
        socketio.emit(
            CONSTANTS.CALLBACK_PUBLISH_STATUS_FAILED,
            {
                "msg": f"Unsuccessful publish for {topic} as the status code is {status_code} with status message "
                f"{status_message}"
            },
            namespace=CONSTANTS.NAMESPACE,
        )


def on_receive_event_handler(socketio, lock_pubsub, utransport, topic, payload: UPayload):
    try:
        topic = "up:" + topic
        topic_class = protobuf_autoloader.get_topic_map()[topic]
        res = common_util.get_class(topic_class)
        any_message = any_pb2.Any()
        any_message.ParseFromString(payload.value)
        res = RpcMapper.unpack_payload(any_message, res)
        original_members = MessageToDict(res, preserving_proto_field_name=True, including_default_value_fields=True)
        members = flatten_dict(original_members)
        json_res = {
            "type": "OnTopicUpdate",
            "transport": utransport,
            "topic": topic,
            "status": "Success",
            "message": original_members,
        }
        save_pub_sub_data(socketio, lock_pubsub, json_res)

        time.sleep(0.5)
        socketio.emit(
            CONSTANTS.CALLBACK_ONEVENT_RECEIVE,
            {"json_data": members, "original_json_data": original_members, "topic": topic},
            namespace=CONSTANTS.NAMESPACE,
        )
    except Exception as ex:
        logger.error("Exception occurs inside onTopicUpdate:", exc_info=ex)
