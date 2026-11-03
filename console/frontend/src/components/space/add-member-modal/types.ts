export interface InviteUser {
  uid: string;
  nickname?: string;
  mobile: string;
  avatar?: string;
  status?: number;
  role?: string;
  username?: string;
}

export interface SelectedInviteUser extends InviteUser {
  role: string;
}
