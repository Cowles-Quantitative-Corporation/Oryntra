// Read only current parent and its direct owned worker; no process inventory.
#include <libproc.h>
#include <sys/resource.h>
#include <sys/proc_info.h>
#include <unistd.h>
#include <stdint.h>
#include <errno.h>
struct sample {uint64_t parent_rss,child_rss,child_start,child_exit;int error,descendants;};
int owned_sample(int child,uint64_t expected,struct sample* out){
 struct rusage_info_v2 parent={0},worker={0};struct proc_bsdinfo bsd={0};
 *out=(struct sample){0};
 if(proc_pid_rusage(getpid(),RUSAGE_INFO_V2,(rusage_info_t*)&parent)!=0){out->error=errno?errno:EIO;return 1;}
 out->parent_rss=parent.ri_resident_size;
 if(child==0)return 0;
 if(child<=0||child==getpid()){out->error=EINVAL;return 2;}
 int count=proc_pidinfo(child,PROC_PIDTBSDINFO,0,&bsd,sizeof(bsd));
 if(count!=(int)sizeof(bsd)||bsd.pbi_ppid!=(uint32_t)getpid()){out->error=errno?errno:ECHILD;return 3;}
 if(proc_pid_rusage(child,RUSAGE_INFO_V2,(rusage_info_t*)&worker)!=0){out->error=errno?errno:EIO;return 4;}
 out->child_rss=worker.ri_resident_size;out->child_start=worker.ri_proc_start_abstime;out->child_exit=worker.ri_proc_exit_abstime;
 if(expected&&expected!=out->child_start){out->error=ESTALE;return 5;}
 int children[2]={0};errno=0;int bytes=proc_listchildpids(child,children,sizeof(children));
 if(bytes<0||(bytes==0&&errno)){out->error=errno?errno:EIO;return 6;}
 out->descendants=bytes>0?1:0;
 if(out->descendants){out->error=ECHILD;return 7;}
 if(out->child_exit){out->error=ESRCH;return 8;}
 return 0;
}

// Only called while the session leader is still our unreaped direct child.
int owned_group_state(int leader){
 if(leader<=0)return -1;
 int ids[3]={0};errno=0;int n=proc_listpgrppids(leader,ids,sizeof(ids));
 if(n<0||errno)return -2;
 for(int i=0;i<3;i++)if(ids[i]>0&&ids[i]!=leader)return 1;
 return 0;
}
