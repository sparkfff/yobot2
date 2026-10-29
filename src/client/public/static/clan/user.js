var gs_offset = {
    jp: 4,
    tw: 5,
    kr: 4,
    cn: 5
};

function pad2(num) {
    return String(num).padStart(2, '0');
}

function ts2ds(timestamp) {
    var d = new Date();
    d.setTime(timestamp * 1000);
    return d.getFullYear() + '/' + pad2(d.getMonth() + 1) + '/' + pad2(d.getDate());
}
var vm = new Vue({
    el: '#app',
    data: {
        exporting: false,
        isLoading: true,
        challengeData: [],
        activeIndex: '5',
        qqid: 0,
        nickname: '',
        tempList: [0, 1, 2, 3, 4, 5],
        members: [],
    },
    mounted() {
        var thisvue = this;
        var pathname = window.location.pathname.split('/');
        thisvue.qqid = parseInt(pathname[pathname.length - 2]);
        axios.all([
            axios.post('../api/', {
                action: 'get_user_challenge',
                csrf_token: csrf_token,
                qqid: thisvue.qqid,
            }),
            axios.post('../api/', {
                action: 'get_member_list',
                csrf_token: csrf_token,
            }),
        ]).then(axios.spread(function (res, memres) {
            if (res.data.code != 0) {
                thisvue.$alert(res.data.message, '获取记录失败');
                return;
            }
            if (memres.data.code != 0) {
                thisvue.$alert(memres.data.message, '获取成员失败');
                return;
            }
            thisvue.members = memres.data.members;
            thisvue.nickname = res.data.user_info.nickname;
            thisvue.refresh(res.data.challenges, res.data.game_server);
            thisvue.isLoading = false;
        })).catch(function (error) {
            thisvue.$alert(error, '获取数据失败');
        });
    },
    methods: {
        find_name: function (qqid) {
            for (m of this.members) {
                if (m.qqid == qqid) {
                    return m.nickname;
                }
            };
            return qqid;
        },
        csummary: function (cha) {
            if (cha == undefined) {
                return '';
            }
            return `(${cha.cycle}-${cha.boss_num}) <a class="digit${cha.damage.toString().length}">${cha.damage}</a>`;
        },
        behalf: function (cha) {
            if (cha == undefined) {
                return '';
            }
            if (cha.behalf) {
                return `<a class="digit${cha.behalf.toString().length}">${this.find_name(cha.behalf)} 代刀</a>`;
            }
        },
        cdetail: function (cha) {
            if (cha == undefined) {
                return '';
            }
            var nd = new Date();
            nd.setTime(cha.challenge_time * 1000);
            var detailstr = nd.toLocaleString('chinese', {
                hour12: false,
                timeZone: 'asia/shanghai'
            }) + '\n';
            detailstr += cha.cycle + '周目' + cha.boss_num + '号boss\n';
            detailstr += (cha.health_remain + cha.damage).toLocaleString(options = {
                timeZone: 'asia/shanghai'
            }) + '→' + cha.health_remain.toLocaleString(options = {
                timeZone: 'asia/shanghai'
            });
            if (cha.message) {
                detailstr += '\n留言：' + cha.message;
            }
            return detailstr;
        },
        arraySpanMethod: function ({
            row,
            column,
            rowIndex,
            columnIndex
        }) {
            if (columnIndex >= 2) {
                if (columnIndex % 2 == 0) {
                    var detail = row.detail[columnIndex - 2];
                    if (detail != undefined && detail.health_remain != 0) {
                        return [1, 2];
                    }
                } else {
                    var detail = row.detail[columnIndex - 3];
                    if (detail != undefined && detail.health_remain != 0) {
                        return [0, 0];
                    }
                }
            }
        },
        refresh: function (challenges, game_server) {
            var thisvue = this;
            var m = {
                pcrdate: -1
            };
            for (c of challenges) {
                var pcrdate = ts2ds(c.challenge_time - (gs_offset[game_server] * 3600));
                if (m.pcrdate != pcrdate) {
                    if (m.pcrdate != -1) {
                        thisvue.challengeData.push(m);
                    }
                    m = {
                        pcrdate: pcrdate,
                        finished: 0,
                        detail: [],
                    }
                }

                for (id of this.tempList) {
                    if (!m.detail[id]) {
                        if (id % 2 == 1 && c.is_continue && m.detail[id - 1] && m.detail[id - 1].health_remain == 0) {
                            m.detail[id] = c;
                            break;
                        } else if (id % 2 == 0) {
                            m.detail[id] = c;
                            break;
                        }
                    }
                }

                if (c.is_continue) {
                    m.finished += 0.5;
                } else {
                    if (c.health_remain != 0) {
                        m.finished += 1;
                    } else {
                        m.finished += 0.5;
                    }
                }
            }
            if (m.pcrdate != -1) {
                thisvue.challengeData.push(m);
            }
        },
        viewInExcel: function () {
            if (this.exporting) return;
            this.exporting = true;
            var thisvue = this;
            return ClanExcel.personal(this.challengeData, {nickname: this.nickname, findName: this.find_name}).catch(function () {
                thisvue.$alert('导出失败，请稍后重试。', '导出 Excel');
            }).then(function () {
                thisvue.exporting = false;
            });
        },
        handleTitleSelect(key, keyPath) {
            switch (key) {
                case '1':
                    window.location = '../';
                    break;
                case '2':
                    window.location = '../subscribers/';
                    break;
                case '3':
                    window.location = '../progress/';
                    break;
                case '4':
                    window.location = '../statistics/';
                    break;
                case '5':
                    window.location = `../my/`;
                    break;
                case '6':
                    window.location = `../clan-rank/`;
                    break;
            }
        },
    },
    delimiters: ['[[', ']]'],
})